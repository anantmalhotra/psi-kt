import math, random, sys, os
import numpy as np
from collections import defaultdict

from tqdm import tqdm

import scipy
import scipy.constants
import scipy.stats
import scipy.optimize

import networkx as nx 

import ipdb
import torch
import torch.nn as nn

from utils import utils
from models.BaseModel import BaseModel



class BaseLearnerModel(BaseModel):
    def __init__(self, 
                 mode, 
                 device='cpu', 
                 logs=None):
        super(BaseLearnerModel, self).__init__()
        self.mode = mode
        self.device = device
        self.logs = logs
        self.optimizer = None
        
        self.model_path = os.path.join(logs.args.log_path, 'Model/Model_{}_{}.pt')
        
    @staticmethod
    def _find_whole_stats(all_feature, t, items, num_node):
        '''
        Args:
            all_feature: [bs, 1, num_step, 3]
            items/t: [bs, num_step]
        '''
        all_feature = all_feature.long()
        device = all_feature.device
        num_seq, num_step = t.shape
        
        # Allocate memory without initializing tensors
        whole_stats = torch.zeros((num_seq, num_node, num_step, 3), device=device, dtype=torch.int64)
        whole_last_time = torch.zeros((num_seq, num_node, num_step+1), device=device, dtype=torch.int64)
        
        # Precompute index tensor
        seq_indices = torch.arange(num_seq, device=device)
        
        # Set initial values for whole_last_time
        whole_last_time[seq_indices, items[:,0], 1] = t[:, 0]

        # Loop over time steps
        for i in range(1, num_step):
            cur_item = items[:, i] # [num_seq, ] 
            cur_feat = all_feature[:,0,i] # [bs, 1, 3] 
            
            # Accumulate whole_stats
            whole_stats[:,:,i] = whole_stats[:,:,i-1] # whole_stats[:,:,i-1] # 
            whole_stats[seq_indices, cur_item, i] = cur_feat
            
            whole_last_time[:,:,i+1] = whole_last_time[:,:,i] # + whole_last_time[seq_indices,:,i]
            whole_last_time[seq_indices, cur_item, i+1] = t[:, i]

        return whole_stats, whole_last_time


    @staticmethod
    def _compute_all_features(num_seq, num_node, time_step, device, stats_cal_on_fly=False, items=None, stats=None):
        if stats_cal_on_fly or items is None:
            item_start = items[:, 0]
            all_feature = torch.zeros((num_seq, num_node, 3), device=device)
            all_feature[torch.arange(0, num_seq), item_start, 0] += 1
            all_feature[torch.arange(0, num_seq), item_start, 2] += 1
            all_feature = all_feature.unsqueeze(-2).tile((1, 1, time_step, 1))
        else:
            all_feature = stats.float()  # [num_seq/bs, num_node, num_time_step, 3]
        return all_feature


    @staticmethod
    def _initialize_parameter(shape, device):
        param = nn.Parameter(torch.empty(shape, device=device))
        nn.init.xavier_uniform_(param)
        return param
    

    def get_feed_dict(self, corpus, data, batch_start, batch_size, phase):
        batch_end = min(len(data), batch_start + batch_size)
        real_batch_size = batch_end - batch_start
        
        feed_dict_keys = {
            'skill_seq': 'skill_seq',
            'label_seq': 'correct_seq',
            'time_seq': 'time_seq',
            'problem_seq': 'problem_seq',
            'num_history': 'num_history',
            'num_success': 'num_success',
            'num_failure': 'num_failure',
            'user_id': 'user_id'
            }
        
        feed_dict = utils._get_feed_dict(
            keys=feed_dict_keys, 
            data=data, 
            start=batch_start, 
            batch_size=real_batch_size, 
        ) # [batch_size, seq_len]
        
        return feed_dict
    
    
    def forward(self, feed_dict):
        skills = feed_dict['skill_seq']      # [batch_size, seq_len]
        times = feed_dict['time_seq']        # [batch_size, seq_len]
        labels = feed_dict['label_seq']      # [batch_size, seq_len]

        bs, _ = labels.shape
        self.num_seq = bs
        
        x0 = torch.zeros((bs, self.num_node), device=self.device)
        if self.num_node > 1:
            x0[torch.arange(bs), skills[:,0]] += labels[:, 0]
            items = skills
        else: 
            x0[:, 0] += labels[:, 0]
            items = None
        
        stats = torch.stack([feed_dict['num_history'], feed_dict['num_success'], feed_dict['num_failure']], dim=-1)
        stats = stats.unsqueeze(1)
        
        out_dict = self.simulate_path(
            x0=x0, 
            t=times, 
            items=items,
            user_id=feed_dict['user_id'],
            stats=stats,
        )
        
        out_dict.update({
            'prediction': out_dict['x_item_pred'],
            'label': labels.unsqueeze(1) # [bs, 1, time]
        })
        
        return out_dict
    
    
##########################################################################################
# HLR Model
##########################################################################################
from enum import Enum

class HLR(BaseLearnerModel):
    def __init__(self, 
                 theta=None, 
                 base=2., 
                 num_seq=1, 
                 num_node=1, 
                 mode='train', 
                 nx_graph=None,
                 device='cpu',
                 logs=None):
        '''
        TODO:
            multiple nodes have bugs 
        Modified from:
            https://github.com/duolingo/halflife-regression/blob/0041df0dcd436bf1b4aa7a17a020d9c670db70d8/experiment.py
        Args:
            theta: [bs/num_seq, num_node, 3]; should be 3D vector indicates the parameters of the model; 
                the näive version is to compute the dot product of theta and [N_total, N_success, N_failure]
            base: the base of HLR model
            num_seq: when mode==synthetic, it is the number of sequences to generate;
                is mode==train, it is the number of batch size
            items: [bs/num_seq, time_step]
            mode: [synthetic, train]; synthetic is to generate new sequences based on given theta; train is to 
                train the parameters theta given observed data.
            device: cpu or cuda to put all variables and train the model
        '''
        super().__init__(mode=mode, device=device, logs=logs)
        self.num_node = num_node
        self.num_seq = num_seq
        self.base = base
        
        if num_node > 1:
            self.adj = torch.tensor(nx_graph, device=self.device) 
            assert(self.adj.shape[-1] == num_node)
        else: 
            self.adj = None

        # Training mode choosing
        class ThetaShape(Enum):
            SIMPLE_SPLIT_TIME = (1, 1, 3)
            SIMPLE_SPLIT_LEARNER = (1, 1, 3)
            LS_SPLIT_TIME = (num_seq, 1, 3)
            NS_SPLIT_TIME = (1, num_node, 3)
            NS_SPLIT_LEARNER = (1, num_node, 3)
            LN_SPLIT_TIME = (num_seq, num_node, 3)
        if mode == 'synthetic':
            self.theta = torch.tensor(theta, device=device).float()
        else:
            try:
                shape = ThetaShape[mode.upper()].value
            except KeyError:
                raise ValueError(f"Invalid mode: {mode}")
            self.theta = self._initialize_parameter(shape, device)


    @staticmethod
    def hclip(h):
        '''
        bound min/max half-life
        '''
        MIN_HALF_LIFE = torch.tensor(15.0 / (24 * 60), device=h.device)    # 15 minutes
        MAX_HALF_LIFE = torch.tensor(274., device=h.device)                # 9 months
        return torch.min(torch.max(h, MIN_HALF_LIFE), MAX_HALF_LIFE)
    
    @staticmethod
    def pclip(p):
        '''
        bound min/max model predictions (helps with loss optimization)
        '''
        MIN_P = torch.tensor(0.0001, device=p.device)
        MAX_P = torch.tensor(0.9999, device=p.device)
        return torch.min(torch.max(p, MIN_P), MAX_P)
    
    def simulate_path(self, x0, t, items=None, stats_cal_on_fly=False, stats=None, user_id=None):
        '''
        Args:
            x0: shape[num_seq/bs, num_node]; the initial state of the learner model
            t: shape[num_seq/bs, num_time_step]
            items: [num_seq/bs, num_time_step]; 
                ** it cannot be None when mode=synthetic
            stats_cal_on_fly: whether calculate the stats of history based on the prediction 
                ** TODO test. it causes gradient error now
            stats: [num_seq/bs, num_node, num_time_step, 3]; it contains [N_total, N_success, N_failure]
        '''
        
        torch.autograd.set_detect_anomaly(True) 
        assert t.numel() > 0 # check if tensor is not empty
        eps = 1e-6
        num_node = x0.shape[-1]
        num_seq, time_step = t.shape

        dt = torch.diff(t).unsqueeze(1) 
        dt = torch.tile(dt, (1, num_node, 1))/60/60/24 + eps # [bs, num_node, time-1]

        # ----- compute the stats of history -----
        if items == None or num_node == 1:
            items = torch.zeros_like(t, device=self.device, dtype=torch.long)
        all_feature = self._compute_all_features(
            num_seq, num_node, time_step, self.device, stats_cal_on_fly, items, stats
        )
        whole_stats, whole_last_time = self._find_whole_stats(
            all_feature, t, items, num_node
        )

        # ----- adapt to different modes -----
        theta_map = {
            'simple': self.theta,
            'ls_split_time': self.theta[user_id],
            'ns_': torch.tile(self.theta, (num_seq, 1, 1)),
            'ln_': self.theta[user_id]
        }
        batch_theta = None
        for mode, value in theta_map.items():
            if mode in self.mode:
                batch_theta = value
                break
        
        # ----- simulate the path -----
        x_pred = [x0]
        x_item_pred = [x0[torch.arange(num_seq), items[:, 0]]]
        half_lifes = [torch.zeros_like(x0, device=self.device)]
        
        for i in range(1, time_step):
            cur_item = items[:, i] # [num_seq, ] 
            cur_dt = (t[:, None, i] - whole_last_time[..., i]) + eps #/60/60/24 + eps # [bs, num_node]
            cur_feat = whole_stats[:, :, i]
            
            feat = torch.mul(cur_feat, batch_theta).sum(-1)
            # feat = torch.minimum(feat, torch.tensor(1e2))
            half_life = self.hclip(self.base ** feat)
            p_all = self.pclip(self.base ** (-cur_dt/half_life)) # [bs, num_node]
            p_item = p_all[torch.arange(num_seq), cur_item] # [bs, ]
            
            if stats_cal_on_fly or self.mode=='synthetic':
                success = nn.functional.gumbel_softmax(torch.log(p_item), hard=True) # TODO
                success = success.unsqueeze(-1)
                all_feature[torch.arange(num_seq), cur_item, i:, 0] += 1
                all_feature[torch.arange(num_seq), cur_item, i:, 1] += success
                all_feature[torch.arange(num_seq), cur_item, i:, 2] += 1-success

            half_lifes.append(half_life)
            x_item_pred.append(p_item)
            x_pred.append(p_all)
        
        half_lifes = torch.stack(half_lifes, -1)
        x_pred = torch.stack(x_pred, -1)
        x_item_pred = torch.stack(x_item_pred, -1).unsqueeze(1)

        params = {
            # NOTE: the first element of the following values in out_dict is not predicted
            'half_life': half_lifes,               # [bs, num_node, times]
            'x_item_pred': x_item_pred,            # [bs, 1, times]
            'x_all_pred': x_pred,                  # [bs, num_node, times]
            'num_history': all_feature[..., 0],    # [bs, num_node, times]
            'num_success': all_feature[..., 1],
            'num_failure': all_feature[..., 2],
        }
        
        return params
        
        
    def loss(self, feed_dict, out_dict, metrics=None):
        losses = {}
        
        pred = out_dict['prediction']
        label = out_dict['label']

        loss_fn = torch.nn.BCELoss()
        bceloss = loss_fn(pred, label.float())
        losses['loss_total'] = bceloss
        
        if metrics:
            pred = pred.detach().cpu().data.numpy()
            label = label.detach().cpu().data.numpy()
            evaluations = BaseModel.pred_evaluate_method(pred, label, metrics)
            losses.update(evaluations)
            
        if 'simple' in self.mode: # TODO
            losses['theta_0'] = self.theta.clone()[0,0,0]
            losses['theta_1'] = self.theta.clone()[0,0,1]
            losses['theta_2'] = self.theta.clone()[0,0,2]
        
        return losses

        
    # def iterate_update(self, inputs, n_iter, learning_rate=1e-3): 
        # t_data, x_data, stats = inputs
        # x0 = x_data[:, :1]
        # x_gt = x_data[:, 1:]
        
        # LN2 = torch.tensor(math.log(2.), device=t_data.device)
        # l2wt = torch.tensor(0.1, device=t_data.device)
        # sigma = torch.tensor(1.0, device=t_data.device)
        
        # loss_fn = torch.nn.BCELoss()
        
        # for _ in range(n_iter):
        #     x_pred, params = self.simulate_path(x0=x0, t=t_data, stats=stats)
        #     p = params['x_item_pred']
        #     h = params['half_life']

        #     # dlp_dw = 2.*(p-x_gt)*(LN2**2)*p*(torch.log(torch.diff(t_data))/h)
        #     dlp_dw = 2.*(p-x_gt)*(LN2**2)*p*((torch.diff(t_data))/60/60/24/h)
            
        #     bceloss = loss_fn(p, x_gt.float())
            
        #     fcounts = defaultdict(int)                  
        #     for k in range(3):
        #         x_k = stats[..., 1:, k]                                                           
        #         rate = (1./(1+x_gt)) * learning_rate / torch.sqrt(torch.tensor(1 + fcounts[k], device=self.device))
                
        #         self.theta[k] -= (rate * dlp_dw * x_k).sum()/rate.shape[-1]

        #         # L2 regularization update
        #         self.theta[k] -= (rate * l2wt * self.theta[k]).sum() / sigma**2 /rate.shape[-1]

        #         # increment feature count for learning rate
        #         fcounts[k] += 1
        #     # self.theta = torch.nn.functional.normalize(self.theta, p=1.0, dim = 0)
        #     # print(self.theta)
        
        
##########################################################################################
# PPE Model
##########################################################################################


class PPE(BaseLearnerModel):
    def __init__(self, 
                 lr=0.1, 
                 variable_x=0.6,
                 variable_b=0.04,
                 variable_m=0.08,
                 variable_tau=0.9,
                 variable_s=0.04,
                 num_seq=1, 
                 num_node=1,
                 mode='train', 
                 nx_graph=None,
                 device='cpu',
                 logs=None,
                 ):
        '''
        Args:
            lr:
        '''
        super().__init__(mode=mode, device=device, logs=logs)
        self.num_node = num_node
        self.num_seq = num_seq

        if num_node > 1:
            self.adj = torch.tensor(nx_graph, device=self.device) 
            assert(self.adj.shape[-1] == num_node)
        else: 
            self.adj = None

        # Training mode choosing
        class ThetaShape(Enum):
            SIMPLE_SPLIT_TIME = (1, 1, 1)
            SIMPLE_SPLIT_LEARNER = (1, 1, 1)
            LS_SPLIT_TIME = (num_seq, 1, 1)
            NS_SPLIT_TIME = (1, num_node, 1)
            NS_SPLIT_LEARNER = (1, num_node, 1)
            LN_SPLIT_TIME = (num_seq, num_node, 1)
            
        if mode == 'synthetic':
            self.variable_x = torch.tensor(variable_x, device=device).float()
            self.variable_b = torch.tensor(variable_b, device=device).float() 
            self.variable_m = torch.tensor(variable_m, device=device).float()
            self.variable_tau = torch.tensor(variable_tau, device=device).float()
            self.variable_s = torch.tensor(variable_s, device=device).float()
            self.lr = torch.tensor(lr, device=device).float()
        else:
            try:
                shape = ThetaShape[mode.upper()].value
            except KeyError:
                raise ValueError(f"Invalid mode: {mode}")
            self.lr = self._initialize_parameter(shape, device)
            self.variable_x = self._initialize_parameter(shape, device)
            self.variable_b = self._initialize_parameter(shape, device)
            self.variable_m = self._initialize_parameter(shape, device)
            
            tau = torch.ones_like(self.lr, device=device) * 0.9
            s = torch.ones_like(self.lr, device=device) * 0.04
            self.variable_tau = nn.Parameter(tau, requires_grad=False)
            self.variable_s = nn.Parameter(s, requires_grad=False)

        
    def simulate_path(self, x0, t, items=None, stats=None, user_id=None, stats_cal_on_fly=False): 
        '''
        Args:
            x0: shape[num_seq/bs, num_node]; the initial state of the learner model
            t: shape[num_seq/bs, num_time_step]
            items: [num_seq/bs, num_time_step]; 
                ** it cannot be None when mode=synthetic
            stats_cal_on_fly: whether calculate the stats of history based on the prediction 
                ** TODO test. it causes gradient error now
            stats: [num_seq/bs, num_node, num_time_step, 3]; it contains [N_total, N_success, N_failure]
        '''
        assert t.numel() > 0 # check if tensor is not empty
        eps = 1e-6
        num_node = x0.shape[-1]
        num_seq, time_step = t.shape
        
        scale_factor = 1 # 1000
        dt = torch.diff(t).unsqueeze(1) 
        dt = torch.tile(dt, (1, num_node, 1))/60/60/24/scale_factor + eps # [bs, num_node, time-1]
        
        # ----- compute the stats of history -----
        if items == None or num_node == 1:
            items = torch.zeros_like(t, device=self.device, dtype=torch.long)
        all_feature = self._compute_all_features(
            num_seq, num_node, time_step, self.device, stats_cal_on_fly, items, stats
        )
        whole_stats, whole_last_time = self._find_whole_stats(
            all_feature, t, items, num_node
        )

        # ----- adapt to different modes -----
        if 'simple' in self.mode:
            user_id = Ellipsis
        elif 'ls_' in self.mode or 'ln_' in self.mode:
            user_id = user_id

        batch_lr = torch.relu(self.lr[user_id]) + eps
        batch_x = torch.sigmoid(self.variable_x[user_id]) + eps
        batch_b = torch.relu(self.variable_b[user_id]) + eps
        batch_m = torch.relu(self.variable_m[user_id]) + eps
        batch_tau = self.variable_tau[user_id]
        batch_s = self.variable_s[user_id]
        
        # ----- simulate the path -----
        t = torch.tile(t.unsqueeze(1), (1,num_node,1))
        drs = []
        x_pred = [x0.unsqueeze(-1)]
        x_item_pred = [x0[torch.arange(0, num_seq), items[:,0]][:, None]]
        
        for i in range(1, time_step):
            cur_item = items[:, i] # [num_seq, ] 
            
            # for PPE part 
            # - small d (decay)
            cur_repeat = whole_stats[:, :, i, 0]
            cur_history_last_time = whole_last_time[:, :, :i+1] # [bs, num_node, i+1]
            lags = torch.diff(cur_history_last_time) / (60 * 60 * 24 * scale_factor) + eps # [bs, num_node, i]
            lag_mask = (lags > 0)
            dn = ((1 / torch.log(abs(lags + eps) + np.e)) * lag_mask).sum(dim=-1) / (cur_repeat + eps) # [bs, num_node]
            dn = batch_b + batch_m * dn.unsqueeze(-1) # [bs, num_node, 1]

            # - big T
            small_t = (t[..., i:i+1] - whole_last_time[..., :i+1])/60/60/24/scale_factor + eps
            # cur_t.unsqueeze(-1) - cur_item_times # [bs, times]
            # mask1 = (cur_item_times!=0)
            # small_t *= mask1
            
            # small_t = torch.minimum(small_t, torch.tensor(1e2))
            big_t = torch.pow(small_t+eps, batch_x)/(torch.sum(torch.pow(small_t+eps, batch_x), 1, keepdims=True) + eps)
            big_t = torch.sum(big_t * small_t, -1)[..., None] # [bs, num_node]
            
            big_t_mask = (big_t!=0)
            mn = torch.pow((whole_stats[:,:,i:i+1,0]+1), batch_lr) * \
                        torch.pow((big_t + eps), -dn) * big_t_mask
            
            pn = 1/(1 + torch.exp((batch_tau - mn)/(batch_s + eps) + eps) + eps)
            
            # ----- update the stats -----    
            if stats_cal_on_fly or self.mode=='synthetic':
                success = (pn>=0.5)*1
                fail = (pn<0.5)*1

                all_feature[torch.arange(num_seq), cur_item, i:, 0] += 1
                all_feature[torch.arange(num_seq), cur_item, i:, 1] += success
                all_feature[torch.arange(num_seq), cur_item, i:, 2] += 1-success
            
            drs.append(dn)
            x_pred.append(pn)
            x_item_pred.append(pn[torch.arange(num_seq), cur_item])
            
        drs = torch.cat(drs, -1) # # [num_seq, num_node, time_step-1]
        x_pred = torch.cat(x_pred, -1) # [num_seq, num_node, time_step]
        x_item_pred = torch.stack(x_item_pred, -1)
        params = {
            'decay_rate': drs,
            'x_item_pred': x_item_pred
        }
        
        return params

        
    def loss(self, feed_dict, out_dict, metrics=None):
        losses = defaultdict(lambda: torch.zeros((), device=self.device))
        
        pred = out_dict['x_item_pred']
        label = out_dict['label']
        
        loss_fn = torch.nn.BCELoss()
        bceloss = loss_fn(pred, label.float())
        losses['loss_total'] = bceloss
        
        if metrics != None:
            pred = pred.detach().cpu().data.numpy()
            label = label.detach().cpu().data.numpy()
            evaluations = BaseModel.pred_evaluate_method(pred, label, metrics)
            losses.update(evaluations)
        
        losses['learning_rate'] = self.lr.clone()[0,0]
        losses['variable_x'] = self.variable_x.clone()[0,0]
        losses['variable_b'] = self.variable_b.clone()[0,0]
        losses['variable_m'] = self.variable_m.clone()[0,0]
        losses['variable_tau'] = self.variable_tau.clone()[0,0]
        losses['variable_s'] = self.variable_s.clone()[0,0]
        losses['variable_b'] = self.variable_b.clone()[0,0]
            
        return losses
    
    
        
        
##########################################################################################
# OU Process
##########################################################################################

class VanillaOU(BaseLearnerModel):
    def __init__(self, 
                 mean_rev_speed=None, 
                 mean_rev_level=None, 
                 vola=None, 
                 num_seq=1, 
                 num_node=1,
                 mode='train',
                 nx_graph=None,
                 device='cpu', 
                 logs=None):
        '''
        Modified from 
            https://github.com/jwergieluk/ou_noise/tree/master/ou_noise
            https://github.com/felix-clark/ornstein-uhlenbeck/blob/master/ornstein_uhlenbeck.py
            https://github.com/369geofreeman/Ornstein_Uhlenbeck_Model/blob/main/research.ipynb
        Args:
            mean_rev_speed: [bs/1, 1] or scalar
            mean_rev_level: [bs/1, 1] or scalar
            vola: [bs/1, 1] or scalar
            num_seq: when training mode, the num_seq will be automatically the number of batch size; 
                     when synthetic mode, the num_seq will be the number of sequences to generate
            mode: can be 'training' or 'synthetic'
            device:  
        '''
        super().__init__(mode=mode, device=device, logs=logs)
        self.num_node = num_node
        self.num_seq = num_seq

        if num_node > 1:
            self.adj = torch.tensor(nx_graph, device=self.device) 
            assert(self.adj.shape[-1] == num_node)
        else: self.adj = None
        
        # Training mode choosing
        if 'simple_' in mode: # global parameter theta for every learner and node
            speed = torch.rand((1, 1, 1), device=device)
            level = torch.rand((1, 1, 1), device=device)
            vola = torch.rand((1, 1, 1), device=device)
            self.mean_rev_speed = nn.Parameter(speed, requires_grad=True)
            self.mean_rev_level = nn.Parameter(level, requires_grad=True)
            self.vola = nn.Parameter(vola, requires_grad=True)
            
        elif 'ls_' in mode:
            speed = torch.rand((num_seq, 1, 1), device=device)
            level = torch.rand((num_seq, 1, 1), device=device)
            vola = torch.rand((num_seq, 1, 1), device=device)
            self.mean_rev_speed = nn.Parameter(speed, requires_grad=True)
            self.mean_rev_level = nn.Parameter(level, requires_grad=True)
            self.vola = nn.Parameter(vola, requires_grad=True)
        
        elif 'ns_' in mode:
            speed = torch.rand((1, num_node, 1), device=device)
            level = torch.rand((1, num_node, 1), device=device)
            vola = torch.rand((1, num_node, 1), device=device)
            self.mean_rev_speed = nn.Parameter(speed, requires_grad=True)
            self.mean_rev_level = nn.Parameter(level, requires_grad=True)
            self.vola = nn.Parameter(vola, requires_grad=True)
        
        elif 'ln_' in mode:
            speed = torch.rand((num_seq, num_node, 1), device=device)
            level = torch.rand((num_seq, num_node, 1), device=device)
            vola = torch.rand((num_seq, num_node, 1), device=device)
            self.mean_rev_speed = nn.Parameter(speed, requires_grad=True)
            self.mean_rev_level = nn.Parameter(level, requires_grad=True)
            self.vola = nn.Parameter(vola, requires_grad=True)

        elif mode == 'synthetic':
            assert(mean_rev_speed is not None)
            self.mean_rev_speed = mean_rev_speed
            self.mean_rev_level = mean_rev_level
            self.vola = vola
            
        else:
            raise Exception('It is not a compatible mode')
            
        assert torch.min(self.mean_rev_speed) >= 0
        assert torch.min(self.vola) >= 0

    def variance(self, t, speed=None, vola=None):
        '''
        The variances introduced by the parameter vola, time difference and Wiener process (Gaussian noise)
        Args:
            t: [bs/num_seq, num_node, times-1]; the time difference
            speed: [bs/num_seq, num_node/1]
            vola: [bs/num_seq, num_node/1]
        '''
        eps = 1e-6
        speed = speed if speed is not None else self.mean_rev_speed
        vola = vola if vola is not None else self.vola
        speed = speed.unsqueeze(-1)
        vola = vola.unsqueeze(-1)
        
        return vola * vola * (1.0 - torch.exp(- 2.0 * speed * t)) / (2 * speed + eps)

    def std(self, t, speed=None, vola=None):
        '''
        Args:
            t: [num_seq/bs, num_node, times] usually is the time difference of a sequence
        '''
        return torch.sqrt(self.variance(t, speed, vola) + 1e-6)

    def mean(self, x0, t, speed=None, level=None):
        '''
        Args:
            x0: 
            t: 
        '''
        speed = speed if speed is not None else self.mean_rev_speed
        level = level if level is not None else self.mean_rev_level

        return x0 * torch.exp(-speed * t) + (1.0 - torch.exp(- speed * t)) * level

    def logll(self, x, t, speed=None, level=None, vola=None):
        """
        Calculates log likelihood of a path
        Args:
            t: [num_seq/bs, time_step]
            x: [num_seq/bs, time_step] it should be the same size as t
        Return:
            log_pdf: [num_seq/bs, 1]
        """
        speed = speed if speed is not None else self.mean_rev_speed
        level = level if level is not None else self.mean_rev_level
        vola = vola if vola is not None else self.vola
        
        dt = torch.diff(t)
        dt = torch.log(dt) # TODO TODO
        mu = self.mean(x, dt, speed, level)
        sigma = self.std(dt, speed, vola)
        var = self.variance(dt, speed, vola)

        dist = torch.distributions.normal.Normal(loc=mu, scale=var)
        log_pdf = dist.log_prob(x).sum(-1)
        # log_scale = torch.log(sigma) / 2
        # log_pdf = -((x - mu) ** 2) / (2 * var) - log_scale - torch.log(torch.sqrt(2 * torch.tensor(math.pi,device=device)))

        return log_pdf


    def simulate_path(self, x0, t, items=None, user_id=None, stats_cal_on_fly=None, stats=None):
        """ 
        Simulates a sample path or forward based on the parameters (speed, level, vola)
        dX = speed*(level-X)dt + vola*dB
        ** the num_node here can be considered as multivariate case of OU process 
            while the correlations between nodes wdo not matter
        Args:
            x0: [num_seq/bs, num_node] the initial states for each node in each sequences
            t: [num_seq/bs, time_step] the time points to sample (or interact);
                It should be the same for all nodes
            items: 
        Return: 
            x_pred: [num_seq/bs, num_node, time_step]
        """
        assert len(t) > 0
        eps = 1e-6
        num_node = x0.shape[-1]
        num_seq, time_step = t.shape
        
        # ipdb.set_trace()
        dt_normalize = 60*60*24
        dt = torch.diff(t).unsqueeze(1)/dt_normalize + eps
        dt = torch.tile(dt, (1, num_node, 1)) # [bs, num_node, time-1]
        # dt = torch.log(dt) # TODO to find the right temperature of time difference in different real-world datasets

        if items == None or num_node == 1:
            items = torch.zeros_like(t, device=self.device, dtype=torch.long)
            
        if 'simple' in self.mode:
            batch_speed = torch.tile(
                torch.relu(self.mean_rev_speed)[...,0] + eps,
                (num_seq, num_node))
            batch_level = torch.tile(
                self.mean_rev_level[..., 0],
                (num_seq, num_node))
            batch_vola = torch.tile(
                torch.relu(self.vola)[..., 0] + eps, 
                (num_seq, num_node))
        elif ('ls_' in self.mode) or ('ln' in self.mode):
            batch_speed = torch.relu(self.mean_rev_speed[user_id])[...,0] + eps # TODO 
            batch_level = self.mean_rev_level[user_id][..., 0]
            batch_vola = torch.relu(self.vola[user_id])[..., 0] + eps
        elif 'ns_' in self.mode:
            batch_speed = torch.tile(
                torch.relu(self.mean_rev_speed[user_id])[...,0] + eps,
                (num_seq, 1))
            batch_level = torch.tile(
                self.mean_rev_level[user_id][..., 0],
                (num_seq, 1))
            batch_vola = torch.tile(
                torch.relu(self.vola[user_id])[..., 0] + eps, 
                (num_seq, 1))
        else: 
            batch_speed = None
            batch_level = None
            batch_vola = None
            

        scale = self.std(dt, speed=batch_speed, vola=batch_vola) # [bs, num_node, t-1]
        noise = torch.randn(size=scale.shape, device=self.device)
        
        x_last = x0 
        x_pred = []
        x_pred.append(x_last)
        x_item_pred = []
        x_item_pred.append(x0[torch.arange(0,num_seq), items[:,0]])

        if stats_cal_on_fly or self.mode=='synthetic':
            item_start = items[:, 0]
            all_feature = torch.zeros((num_seq, num_node, 3), device=self.device)
            all_feature[torch.arange(0, num_seq), item_start, 0] += 1
            all_feature[torch.arange(0, num_seq), item_start, 2] += 1
            all_feature = all_feature.unsqueeze(-2).tile((1,1,time_step,1))
        else: 
            all_feature = stats.float() # [num_seq/bs, num_node, num_time_step, 3]
            
        _, whole_last_time = self._find_whole_stats(all_feature, t, items, num_node)

        for i in range(1, time_step):
            cur_item = items[:, i]
            
            cur_dt = (t[:,None,i] - whole_last_time[..., i])/dt_normalize + eps # [bs, num_node]

            x_next = self.mean(x_last, cur_dt, speed=batch_speed, level=batch_level)  # [bs, num_node]
            x_next = x_next + noise[..., i-1] * scale[..., i-1]
            x_pred.append(x_next)
            
            x_pred_item = x_next[torch.arange(0,num_seq), cur_item] # [bs, ]
            x_item_pred.append(x_pred_item)
            
            x_last = x_next
            
        x_pred = torch.stack(x_pred, -1)
        x_item_pred = torch.stack(x_item_pred, -1).unsqueeze(1)

        params = {
            'x_original_item_pred': x_item_pred,        # [bs, 1, times]
            'x_original_all_pred': x_pred,              # [bs, num_node, times]
            'x_all_pred': torch.sigmoid(x_pred),
            'x_item_pred': torch.sigmoid(x_item_pred),
            'std': noise * scale, 
            'user_id': user_id,
            'times': t,
            'items': items,
        }
        
        return params
            
            
    def loss(self, feed_dict, out_dict, metrics=None):
        losses = defaultdict(lambda: torch.zeros((), device=self.device))
        
        pred = out_dict['prediction']
        label = out_dict['label']
        
        loss_fn = torch.nn.BCELoss()
        bceloss = loss_fn(pred, label.float())
        losses['loss_total'] = bceloss
        
        if metrics != None:
            pred = pred.detach().cpu().data.numpy()
            label = label.detach().cpu().data.numpy()
            evaluations = BaseModel.pred_evaluate_method(pred, label, metrics)
        for key in evaluations.keys():
            losses[key] = evaluations[key]
            
        # TODO better visualization
        losses['mean_rev_speed'] = self.mean_rev_speed[0].clone()
        losses['mean_rev_level'] = self.mean_rev_level[0].clone()
        losses['vola'] = self.vola[0].clone()
        
        return losses
  
  
  
  
class GraphOU(VanillaOU):
    '''
    Args:
        mean_rev_speed: 
        mean_rev_level:
        vola:
    '''
    def __init__(self,
                 mean_rev_speed=None, 
                 mean_rev_level=None, 
                 vola=None, 
                 gamma=None,
                 num_seq=1, 
                 num_node=1,
                 mode='train',
                 nx_graph=None,
                 device='cpu', 
                 logs=None):
        super().__init__(mean_rev_speed, mean_rev_level, vola, num_seq, num_node,
                 mode, nx_graph, device, logs)
        
        if 'simple_' in mode: 
            gamma = torch.rand((1, 1, 1), device=device)
            self.gamma = nn.Parameter(gamma, requires_grad=True)
        elif 'ls_' in mode:
            gamma = torch.rand((num_seq, 1, 1), device=device)
            self.gamma = nn.Parameter(gamma, requires_grad=True)
        elif 'ns_' in mode:
            gamma = torch.rand((1, num_node, 1), device=device)
            self.gamma = nn.Parameter(gamma, requires_grad=True)
        elif 'ln_' in mode:
            gamma = torch.rand((num_seq, num_node, 1), device=device)
            self.gamma = nn.Parameter(gamma, requires_grad=True)
            
    
    def simulate_path(self, x0, t, items=None, user_id=None, stats=None): 
        """ 
        Simulates a sample path or forward based on the parameters (speed, level, vola)
        dX = speed*(level-X)dt + vola*dB
        ** the num_node here can be considered as multivariate case of OU process 
            while the correlations between nodes wdo not matter
        Args:
            x0: [num_seq/bs, num_node] the initial states for each node in each sequences
            t: [num_seq/bs, time_step] the time points to sample (or interact);
                It should be the same for all nodes
            items: [num_seq/bs, time_step]
        Return: 
            x_pred: [num_seq/bs, num_node, time_step]
        """
        assert len(t) > 0
        eps = 1e-6
        omega = 0.5
        rho = 50
        self.rho = torch.tensor(rho, device=self.device)
        num_node = x0.shape[-1]
        num_seq, time_step = t.shape

        t = t
        dt = torch.diff(t).unsqueeze(1)/60/60/24 + eps
        dt = torch.tile(dt, (1, num_node, 1)) + eps # [bs, num_node, time-1]
        # dt = torch.log(dt) # TODO to find the right temperature of time difference in different real-world datasets

        if items == None or num_node == 1:
            items = torch.zeros_like(t, device=self.device, dtype=torch.long)
            
        # ipdb.set_trace()
        if 'simple' in self.mode:
            batch_speed = torch.tile(
                torch.relu(self.mean_rev_speed)[...,0] + eps,
                (num_seq, num_node))
            batch_level = torch.tile(
                self.mean_rev_level[..., 0],
                (num_seq, num_node))
            batch_vola = torch.tile(
                torch.relu(self.vola)[..., 0] + eps, 
                (num_seq, num_node))
            batch_gamma = torch.tile(
                self.gamma[..., 0],
                (num_seq, num_node))
        elif ('ls_' in self.mode) or ('ln_' in self.mode):
            batch_speed = torch.relu(self.mean_rev_speed[user_id])[...,0] + eps # TODO 
            batch_level = self.mean_rev_level[user_id][..., 0]
            batch_vola = torch.relu(self.vola[user_id])[..., 0] + eps
            batch_gamma = self.gamma[user_id][..., 0]
        elif 'ns_' in self.mode:
            batch_speed = torch.tile(
                torch.relu(self.mean_rev_speed)[...,0] + eps,
                (num_seq, 1))
            batch_level = torch.tile(
                self.mean_rev_level[..., 0],
                (num_seq, 1))
            batch_vola = torch.tile(
                torch.relu(self.vola)[..., 0] + eps, 
                (num_seq, 1))
            batch_gamma = torch.tile(
                self.gamma[..., 0],
                (num_seq, 1))
            
        # graph
        adj = self.adj.float()
        adj_t = torch.transpose(adj, -1, -2) # TODO test with multiple power of adj
        assert(num_node == adj.shape[-1])
    
        scale = self.std(dt, speed=batch_speed, vola=batch_vola) # [bs, num_node, t-1]
        noise = torch.randn(size=scale.shape, device=self.device)
        
        x_pred = []
        x_item_pred = []
        x_last = x0  # [bs, num_node]
        x_pred.append(x_last)
        x_item_pred.append(x0[torch.arange(0,num_seq), items[:,0]])
        
        # find degree 0
        in_degree = adj_t.sum(dim=-1)
        ind = torch.where(in_degree == 0)[0] # [284,]
        
        
        # NOTE: `whole_last_time` and `dt` is different 
        all_feature = stats.float()
        # whole_stats, whole_last_time = self._find_whole_stats(all_feature, t, items, num_node)
        
        for i in range(1, time_step):
            # TODO no spike
            cur_item = items[:, i] # [num_seq, ] 
            
            empower = torch.einsum('ij, ai->aj', adj_t, x_last)
            empower = (1/(in_degree[None, :] + eps)) * batch_gamma * empower
            empower[:,ind] = 0
            # ipdb.set_trace()
            # stable = torch.pow((success_last/(num_last+eps)), self.rho)
            
            # # Choice 1
            # stable = batch_level
            # tmp_mean_level = omega * empower + (1-omega) * stable
            # tmp_batch_speed = batch_speed
            
            # Choice 2
            stable = batch_speed
            tmp_mean_level = batch_level
            tmp_batch_speed = torch.relu(omega * empower + (1-omega) * stable) + eps

            x_next = self.mean(x_last, dt[..., i-1], speed=tmp_batch_speed, level=tmp_mean_level) # [num_seq/bs, num_node]
            x_next = x_next + noise[..., i-1] * scale[..., i-1]
            x_pred.append(x_next)
            
            x_pred_item = x_next[torch.arange(0,num_seq), cur_item] # [bs, ]
            x_item_pred.append(x_pred_item)
            
            x_last = x_next
        
        x_pred = torch.stack(x_pred, -1)
        x_item_pred = torch.stack(x_item_pred, -1).unsqueeze(1)
        
        params = {
            'x_original_item_pred': x_item_pred,        # [bs, times]
            'x_original_all_pred': x_pred,              # [bs, num_node, times]
            'x_all_pred': torch.sigmoid(x_pred),
            'x_item_pred': torch.sigmoid(x_item_pred),
            'std': noise * scale,
            'user_id': user_id,
            'items': items,
            'times': t,
            
        }
        
        return params
        
        
    def loss(self, feed_dict, out_dict, metrics=None):
        losses = defaultdict(lambda: torch.zeros((), device=self.device))
        
        pred = out_dict['prediction']
        label = out_dict['label']
        
        loss_fn = torch.nn.BCELoss()
        bceloss = loss_fn(pred, label.float())
        losses['loss_total'] = bceloss
        
        if metrics != None:
            pred = pred.detach().cpu().data.numpy()
            label = label.detach().cpu().data.numpy()
            evaluations = BaseModel.pred_evaluate_method(pred, label, metrics)
        for key in evaluations.keys():
            losses[key] = evaluations[key]
            
        # TODO better visualization
        losses['mean_rev_speed'] = self.mean_rev_speed[0].clone()
        losses['mean_rev_level'] = self.mean_rev_level[0].clone()
        losses['vola'] = self.vola[0].clone()
        losses['gamma'] = self.gamma[0].clone()
        
        return losses





