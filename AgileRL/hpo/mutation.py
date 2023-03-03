import torch.optim as optim
import numpy as np
import fastrand

class Mutations():
    def __init__(self, no_mutation, architecture, new_layer_prob, parameters, activation, rl_hp, rl_hp_selection, mutation_sd, rand_seed=1, device='cpu'):
        self.rng = np.random.RandomState(rand_seed)
        self.no_mut = no_mutation
        self.architecture_mut = architecture
        self.new_layer_prob = new_layer_prob
        self.parameters_mut = parameters
        self.activation_mut = activation
        self.rl_hp_mut = rl_hp
        self.rl_hp_selection = rl_hp_selection
        self.mutation_sd = mutation_sd
        self.device = device

    def no_mutation(self, individual):
        individual.mut = 'None'
        return individual

    def mutation(self, population):

        mutation_options = []
        mutation_proba = []
        if self.no_mut:
            mutation_options.append(self.no_mutation)
            mutation_proba.append(float(self.no_mut))
        if self.architecture_mut:
            mutation_options.append(self.architecture_mutate)
            mutation_proba.append(float(self.architecture_mut))
        if self.parameters_mut:
            mutation_options.append(self.parameter_mutation)
            mutation_proba.append(float(self.parameters_mut))
        if self.activation_mut:
            mutation_options.append(self.activation_mutation)
            mutation_proba.append(float(self.activation_mut))
        if self.rl_hp_mut:
            mutation_options.append(self.rl_hyperparam_mutation)
            mutation_proba.append(float(self.rl_hp_mut))

        if len(mutation_options) == 0:
            return population

        mutation_proba = np.array(mutation_proba) / np.sum(mutation_proba)

        mutation_choice = self.rng.choice(mutation_options, len(population), p=mutation_proba)

        mutated_population = []
        for mutation, individual in zip(mutation_choice, population):

            individual = mutation(individual)

            # ONLY NEED TO DEAL WITH TARGETS HERE. OTHERWISE ALWAYS ACTORS
            agent = self.get_algo_nets(individual)
            offspring_actor = getattr(individual, agent['actor']['eval'])
                        
            # Reinitialise target network with frozen weights due to potential mutation in architecture of value network
            ind_target = type(offspring_actor)(**offspring_actor.init_dict)
            ind_target.load_state_dict(offspring_actor.state_dict())
            setattr(individual, agent['actor']['target'], ind_target.to(self.device))

            for critic in agent['critics']:
                offspring_critic = getattr(individual, critic['eval'])
                ind_target = type(offspring_critic)(**offspring_critic.init_dict)
                ind_target.load_state_dict(offspring_critic.state_dict())
                setattr(individual, critic['target'], ind_target.to(self.device))

            mutated_population.append(individual)

        return mutated_population

    def rl_hyperparam_mutation(self, individual):
        rl_params = self.rl_hp_selection
        mutate_param = self.rng.choice(rl_params, 1)[0]

        random_num = self.rng.uniform(0, 1)
        if mutate_param == 'batch_size':
            if random_num > 0.5:
                individual.batch_size = min(128, max(8, int(individual.batch_size * 1.2)))
            else:
                individual.batch_size = min(128, max(8, int(individual.batch_size * 0.8)))
            individual.mut = 'bs'
        elif mutate_param == 'lr':
            if random_num > 0.5:
                individual.lr = min(0.005, max(0.00001, individual.lr * 1.2))
            else:
                individual.lr = min(0.005, max(0.00001, individual.lr * 0.8))
            
            # Reinitialise optim if new lr
            agent = self.get_algo_nets(individual)
            actor_opt = getattr(individual, agent['actor']['optimizer'])
            net_params = getattr(individual, agent['actor']['eval']).parameters()
            setattr(individual, agent['actor']['optimizer'], type(actor_opt)(net_params, lr=individual.lr))
            
            for critic in agent['critics']:
                critic_opt = getattr(individual, critic['optimizer'])
                net_params = getattr(individual, critic['eval']).parameters()
                setattr(individual, critic['optimizer'], type(critic_opt)(net_params, lr=individual.lr)) 
            individual.mut = 'lr'

        return individual

    def activation_mutation(self, individual):
        agent = self.get_algo_nets(individual)

        offspring_actor = getattr(individual, agent['actor']['eval'])
        offspring_actor = self._permutate_activation(offspring_actor)
        setattr(individual, agent['actor']['eval'], offspring_actor.to(self.device))

        for critic in agent['critics']:
            offspring_critic = getattr(individual, critic['eval'])
            offspring_critic = self._permutate_activation(offspring_critic)
            setattr(individual, critic['eval'], offspring_critic.to(self.device))
        
        individual.mut = 'act'
        return individual

    def _permutate_activation(self, network):

        possible_activations = ['relu', 'elu', 'tanh']
        current_activation = network.activation
        possible_activations.remove(current_activation)
        new_activation = self.rng.choice(possible_activations, size=1)[0]
        net_dict = network.init_dict
        net_dict['activation'] = new_activation
        new_network = type(network)(**net_dict)
        new_network.load_state_dict(network.state_dict())
        network = new_network

        return network.to(self.device)

    def parameter_mutation(self, individual):
        agent = self.get_algo_nets(individual)
        offspring_actor = getattr(individual, agent['actor']['eval'])
        offspring_actor = self.classic_parameter_mutation(offspring_actor)
        setattr(individual, agent['actor']['eval'], offspring_actor.to(self.device))
        individual.mut = 'param'
        return individual

    def regularize_weight(self, weight, mag):
        if weight > mag: weight = mag
        if weight < -mag: weight = -mag
        return weight

    def classic_parameter_mutation(self, network):
        mut_strength = self.mutation_sd
        num_mutation_frac = 0.1
        super_mut_strength = 10
        super_mut_prob = 0.05
        reset_prob = super_mut_prob + 0.05

        model_params = network.state_dict()

        potential_keys = []
        for i, key in enumerate(model_params):  # Mutate each param
            if not 'norm' in key:
                W = model_params[key]
                if len(W.shape) == 2:  # Weights, no bias
                    potential_keys.append(key)

        how_many = np.random.randint(1, len(potential_keys) + 1, 1)[0]
        chosen_keys = np.random.choice(potential_keys, how_many, replace=False)

        for key in chosen_keys:
            # References to the variable keys
            W = model_params[key]
            num_weights = W.shape[0] * W.shape[1]
            # Number of mutation instances
            num_mutations = fastrand.pcg32bounded(int(np.ceil(num_mutation_frac * num_weights)))
            for _ in range(num_mutations):
                ind_dim1 = fastrand.pcg32bounded(W.shape[0])
                ind_dim2 = fastrand.pcg32bounded(W.shape[-1])
                random_num = self.rng.uniform(0, 1)

                if random_num < super_mut_prob:  # Super Mutation probability
                    W[ind_dim1, ind_dim2] += self.rng.normal(0, np.abs(super_mut_strength * W[ind_dim1, ind_dim2].item()))
                elif random_num < reset_prob:  # Reset probability
                    W[ind_dim1, ind_dim2] = self.rng.normal(0, 1)
                else:  # mutauion even normal
                    W[ind_dim1, ind_dim2] += self.rng.normal(0, np.abs(mut_strength * W[ind_dim1, ind_dim2].item()))

                # Regularization hard limit
                W[ind_dim1, ind_dim2] = self.regularize_weight(W[ind_dim1, ind_dim2].item(), 1000000)
        return network.to(self.device)


    def architecture_mutate(self, individual):

        agent = self.get_algo_nets(individual)

        offspring_actor = getattr(individual, agent['actor']['eval']).clone()
        offspring_critics = [getattr(individual, critic['eval']).clone() for critic in agent['critics']]

        rand_numb = self.rng.uniform(0, 1)
        if rand_numb < self.new_layer_prob:
            offspring_actor.add_layer()
            for offspring_critic in offspring_critics:
                offspring_critic.add_layer()
        else:
            node_dict = offspring_actor.add_node()
            for offspring_critic in offspring_critics:
                offspring_critic.add_node(**node_dict)

        setattr(individual, agent['actor']['eval'], offspring_actor.to(self.device))
        for offspring_critic, critic in zip(offspring_critics, agent['critics']):
            setattr(individual, critic['eval'], offspring_critic.to(self.device))
           
        individual.mut = 'arch'
        return individual

    
    def get_algo_nets(self, individual):
        if individual.algo == 'DQN':
            nets = {
                'actor': {
                    'eval': 'net_eval',
                    'target': 'net_target',
                    'optimizer': 'optimizer'
                    },
                'critics': []
            }
        elif individual.algo == 'DDPG':
            nets = {
                'actor': {
                    'eval': 'actor',
                    'target': 'actor_target',
                    'optimizer': 'actor_optimizer'
                    },
                'critics': [{
                    'eval': 'critic',
                    'target': 'critic_target',
                    'optimizer': 'critic_optimizer'
                    }]
            }
        return nets