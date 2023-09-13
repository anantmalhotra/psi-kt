import pytest

# I haven't figured out how to import the module from the root directory yet
# I currently use this as the hacky way to import the module...
import sys

sys.path.append("..")

import torch

from knowledge_tracing.groupkt import EPS
from knowledge_tracing.groupkt.groupkt import AmortizedGroupKT
from knowledge_tracing.runner.runner import KTRunner
from knowledge_tracing.utils.logger import Logger

DIM_S = 4

@pytest.fixture
def groupkt():
    class args_example(object):
        def __init__(
            self,
        ):
            self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
            self.learned_graph = "w_gt"
            self.var_log_max = torch.tensor(1.0)
            self.node_dim = 16
            self.num_category = 5
            self.time_dependent_s = 1
            self.num_sample = 1
            
            self.max_step = 50
            self.train_time_ratio = 0.2
            self.test_time_ratio = 0.2
            self.val_time_ratio = 0.2
            
            self.overfit = 1
            self.epoch = 10
            self.batch_size_multiGPU = 32
            self.eval_batch_size = 32
            self.metric = "Accuracy, F1, Recall, Precision, AUC"
            self.early_stop = 1
            self.create_logs = 0
            self.log_path = 'logs'

    nx_graph = torch.randn(10, 10)
    num_node = 10
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
    
    args = args_example()
    logs = Logger(args)
    model = AmortizedGroupKT(num_node=num_node, args=args, device=device, logs=logs, nx_graph=nx_graph)
    
    return model

@pytest.fixture
def ps_dist():  
    ps_mean = torch.randn(2, 1, 10, DIM_S)
    ps_cov_mat = torch.randn(2, 1, 10, DIM_S, DIM_S)
    ps_dist = torch.distributions.MultivariateNormal(ps_mean, ps_cov_mat)
    return ps_dist

@pytest.fixture
def qs_dist():
    # Create a sample MultivariateNormal distribution for qs_dist (replace with actual parameters)
    qs_mean = torch.randn(2, 1, 10, DIM_S)
    qs_cov_mat = torch.randn(2, 1, 10, DIM_S, DIM_S)
    qs_dist = torch.distributions.MultivariateNormal(qs_mean, qs_cov_mat)
    return qs_dist


# Test the _init_weights method
def test_init_weights(groupkt):
    # Call the _init_weights method
    groupkt._init_weights()

    # Check that the model attributes are initialized correctly
    assert isinstance(groupkt.node_dist, VarTransformation)
    assert isinstance(groupkt.gen_s0_mean, torch.Tensor)
    assert isinstance(groupkt.gen_s0_log_var, torch.Tensor)
    assert isinstance(groupkt.gen_z0_mean, torch.Tensor)
    assert isinstance(groupkt.gen_z0_log_var, torch.Tensor)
    assert isinstance(groupkt.gen_st_h, torch.Tensor)
    assert isinstance(groupkt.gen_st_b, torch.Tensor)
    assert isinstance(groupkt.gen_st_log_r, torch.Tensor)
    assert isinstance(groupkt.y_emit, torch.nn.Sigmoid)
    assert isinstance(groupkt.infer_network_emb, torch.nn.Module)
    assert isinstance(groupkt.infer_network_posterior_s, InferenceNet)  # Replace with the actual class name
    assert isinstance(groupkt.infer_network_posterior_z, torch.nn.LSTM)
    assert isinstance(groupkt.infer_network_posterior_mean_var_z, VAEEncoder)  # Replace with the actual class name


def test_st_transition_gen(groupkt, qs_dist):
    
    transition_st_h = groupkt.gen_st_h
    transition_st_b = groupkt.gen_st_b
    assert isinstance(transition_st_h, torch.Tensor)
    assert transition_st_h.shape == (DIM_S, DIM_S)
    assert isinstance(transition_st_b, torch.Tensor)
    assert transition_st_b.shape == (DIM_S,)
    
    # Call the st_transition_gen method
    ps_dist = groupkt.st_transition_gen(qs_dist, eval=False)
    # Check that the output ps_dist is a MultivariateNormal distribution
    assert isinstance(ps_dist, torch.distributions.MultivariateNormal)
    
    
    num_samples = 1000
    qs_sample = qs_dist.sample((num_samples,))  # [num_samples, bs, 1, time, dim_s]
    qs_sample_transition = (
        qs_sample[:, :, :, :-1] @ groupkt.gen_st_h + groupkt.gen_st_b
    )  # [num_samples, bs, 1, time-1, dim_s]
    qs_sample_transition_mean = qs_sample_transition.mean(dim=0)  # [bs, 1, time-1, dim_s]
    qs_sample_transition_cov_mat = qs_sample_transition.var(dim=0)  # [bs, 1, time-1, dim_s, dim_s]
    ps_dist_mean = ps_dist.mean[:,:,1:]
    ps_dist_cov_mat = ps_dist.covariance_matrix[:,:,1:]
    assert torch.allclose(qs_sample_transition_mean, ps_dist_mean, atol=1e-1)
    assert torch.allclose(qs_sample_transition_cov_mat, ps_dist_cov_mat, atol=1e-1)
    
