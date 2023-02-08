import gc
import copy
import os

from time import time
from tqdm import tqdm
import numpy as np
import matplotlib.pyplot as plt
from collections import defaultdict

import torch
from torch.optim import lr_scheduler

from utils import utils
import ipdb

torch.autograd.set_detect_anomaly(True)


class KTRunner(object):
    '''
    This implements the training loop, testing & validation, optimization etc. 

    Args:
        args: the global arguments
        logs: the Logger instance for logging information
    '''

    def __init__(self, args, logs):
        # TODO debug args
        self.overfit = args.overfit 

        self.args = args
        self.epoch = args.epoch
        self.batch_size = args.batch_size_multiGPU 
        self.eval_batch_size = args.eval_batch_size
        
        self.metrics = args.metric.strip().lower().split(',')
        for i in range(len(self.metrics)):
            self.metrics[i] = self.metrics[i].strip()

        self.early_stop = args.early_stop
        self.time = None
        self.logs = logs


    def _check_time(self, start=False):
        if self.time is None or start:
            self.time = [time()] * 2
            return self.time[0]
        tmp_time = self.time[1]
        self.time[1] = time()
        return self.time[1] - tmp_time


    def _build_optimizer(self, model):
        '''
        Choose the optimizer based on the optimizer name in the global arguments.
        The optimizer has the setting of weight decay, and learning rate decay which can be modified in global arguments.

        Args:
            model: the training KT model
        '''
        optimizer_name = self.args.optimizer.lower()
        lr = self.args.lr
        weight_decay = self.args.l2
        lr_decay = self.args.lr_decay
        lr_decay_gamma = self.args.gamma

        if optimizer_name == 'gd':
            self.logs.write_to_log_file("Optimizer: GD")
            optimizer = torch.optim.SGD(model.module.customize_parameters(), lr=lr, weight_decay=weight_decay)
        elif optimizer_name == 'adagrad':
            self.logs.write_to_log_file("Optimizer: Adagrad")
            optimizer = torch.optim.Adagrad(model.module.customize_parameters(), lr=lr, weight_decay=weight_decay)
        elif optimizer_name == 'adadelta':
            self.logs.write_to_log_file("Optimizer: Adadelta")
            optimizer = torch.optim.Adadelta(model.module.customize_parameters(), lr=lr, weight_decay=weight_decay)
        elif optimizer_name == 'adam':
            self.logs.write_to_log_file("Optimizer: Adam")
            optimizer = torch.optim.Adam(model.module.customize_parameters(), lr=lr, weight_decay=weight_decay)
        else:
            raise ValueError("Unknown Optimizer: " + self.optimizer_name)

        scheduler = lr_scheduler.StepLR(optimizer, step_size=lr_decay, gamma=lr_decay_gamma)

        return optimizer, scheduler


    def predict(self, model, corpus, set_name):
        model.eval()
        predictions, labels = [], []
        batches = model.module.prepare_batches(corpus, corpus.data_df[set_name], self.eval_batch_size, phase=set_name)
        for batch in tqdm(batches, leave=False, ncols=100, mininterval=1, desc='Predict'):
            batch = model.module.batch_to_gpu(batch)
            outdict = model(batch)
            prediction, label = outdict['prediction'], outdict['label']
            predictions.extend(prediction.detach().cpu().data.numpy())
            labels.extend(label.detach().cpu().data.numpy())
        return np.array(predictions), np.array(labels)


    def fit(self, model, corpus, epoch_train_data, epoch=-1):  # fit the results for an input set
        """
        epoch_train_data: Index(['user_id', 'skill_seq', 'correct_seq', 'time_seq', 'problem_seq'], dtype='object')
        """
        if model.module.optimizer is None:
            model.module.optimizer, model.module.scheduler = self._build_optimizer(model)
        
        train_losses = defaultdict(list)

        model.train()
        
        batches = model.module.prepare_batches(corpus, epoch_train_data, self.batch_size, phase='train')
        
        for batch in tqdm(batches, leave=False, ncols=100, mininterval=1, desc='Epoch %5d' % epoch):
            # ipdb.set_trace()
            # # TODO for debugging
            for name, param in model.module.named_parameters():
                if param.grad != None:
                    print(name, torch.isfinite(param.grad).all())
                else: print(name)
            for name, param in model.named_parameters():
                if param.grad != None:
                    print(name, torch.isfinite(param.grad).all())
                else: print(name)
                if param.requires_grad:
                    print('Grad:', name)
                    
            batch = model.module.batch_to_gpu(batch)
            model.module.optimizer.zero_grad()
            output_dict = model(batch)
            loss_dict = model.module.loss(batch, output_dict, metrics = self.metrics)

            loss_dict['loss_total'].backward()
            model.module.optimizer.step()
            model.module.scheduler.step()
            
            train_losses = utils.append_losses(train_losses, loss_dict)
            
        # ipdb.set_trace()
        # TODO DEBUG: to visualize the difference of synthetic data adj
        if 'synthetic' in self.args.dataset and epoch%2 == 0:
            import matplotlib.patches as mpatches
            gt_adj = batch['gt_adj']
            _, probs, pred_adj = model.module.var_dist_A.sample_A(num_graph=100)
            print(torch.mean(probs, 0))
            # ipdb.set_trace()
            mat_diff = gt_adj-pred_adj[0,0] 
            mat_diff = mat_diff.int().cpu().detach().numpy()
            im = plt.imshow(mat_diff, interpolation='none', cmap='Blues',aspect='auto',alpha=0.5)

            values = np.unique(mat_diff.ravel())
            colors = [im.cmap(im.norm(value)) for value in values]
            patches = [mpatches.Patch(color=colors[i], label="Level {l}".format(l=values[i]) ) for i in range(len(values))]

            plt.legend(handles=patches, bbox_to_anchor=(1.05, 1), loc=2, borderaxespad=0. )
            plt.savefig(os.path.join(self.args.plotdir, 'adj_diff_epoch{}.png'.format(epoch)))

        # # TODO DEBUG
        # if epoch % 10 == 0:
        #     print(output_dict['prediction'])
        string = self.logs.result_string("train", epoch, train_losses, t=epoch)
        self.logs.write_to_log_file(string)
        self.logs.append_train_loss(train_losses)
        
        model.eval()
        return self.logs.train_results['loss_total'][-1]

    def eva_termination(self, model):
        valid = list(self.logs.valid_results[self.metrics[0]])
        if len(valid) > 20 and utils.non_increasing(valid[-10:]):
            return True
        elif len(valid) - valid.index(max(valid)) > 20:
            return True
        return False


    def train(self, model, corpus):

        assert(corpus.data_df['train'] is not None)
        self._check_time(start=True)

        try:
            for epoch in range(self.epoch):
                gc.collect()
                self._check_time()
                
                if self.overfit > 0:
                    epoch_train_data = copy.deepcopy(corpus.data_df['train'])[:self.overfit] # Index(['user_id', 'skill_seq', 'correct_seq', 'time_seq', 'problem_seq'], dtype='object')
                else:
                    epoch_train_data = copy.deepcopy(corpus.data_df['train'])
                epoch_train_data = epoch_train_data.sample(frac=1).reset_index(drop=True) # Return a random sample of items from an axis of object.

                loss = self.fit(model, corpus, epoch_train_data, epoch=epoch + 1)

                del epoch_train_data
                training_time = self._check_time()

                # # output validation and write to logs
                # valid_result = self.evaluate(model, corpus, 'dev')
                # test_result = self.evaluate(model, corpus, 'test')

                # self.logs.append_test_loss(test_result)
                # self.logs.append_val_loss(valid_result)
                
                self.logs.draw_loss_curves()

                # testing_time = self._check_time()

                # self.logs.write_to_log_file("Epoch {:<3} loss={:<.4f} [{:<.1f} s]\t valid=({}) test=({}) [{:<.1f} s] ".format(
                #              epoch + 1, loss, training_time, utils.format_metric(valid_result),
                #              utils.format_metric(test_result), testing_time))
                             
                # if max(self.logs.valid_results[self.metrics[0]]) == valid_result[self.metrics[0]]:
                #     model.module.save_model(epoch=epoch)
                # if self.eva_termination(model) and self.early_stop:
                #     self.logs.write_to_log_file("Early stop at %d based on validation result." % (epoch + 1))
                #     break

        except KeyboardInterrupt:
            self.logs.write_to_log_file("Early stop manually")
            exit_here = input("Exit completely without evaluation? (y/n) (default n):")
            if exit_here.lower().startswith('y'):
                self.logs.write_to_log_file(os.linesep + '-' * 45 + ' END: ' + utils.get_time() + ' ' + '-' * 45)
                exit(1)

        # Find the best validation result across iterations
        best_valid_epoch = self.logs.valid_results[self.metrics[0]].argmax()
        valid_res_dict, test_res_dict = dict(), dict()
        
        for metric in self.metrics:
            valid_res_dict[metric] = self.logs.valid_results[metric][best_valid_epoch]
            test_res_dict[metric] = self.logs.test_results[metric][best_valid_epoch]
        self.logs.write_to_log_file("\nBest Iter(dev)=  %5d\t valid=(%s) test=(%s) [%.1f s] "
                     % (best_valid_epoch + 1,
                        utils.format_metric(valid_res_dict),
                        utils.format_metric(test_res_dict),
                        self.time[1] - self.time[0]))

        best_test_epoch = self.logs.test_results[self.metrics[0]].argmax()
        for metric in self.metrics:
            valid_res_dict[metric] = self.logs.valid_results[metric][best_test_epoch]
            test_res_dict[metric] = self.logs.test_results[metric][best_test_epoch]
        self.logs.write_to_log_file("Best Iter(test)= %5d\t valid=(%s) test=(%s) [%.1f s] \n"
                     % (best_test_epoch + 1,
                        utils.format_metric(valid_res_dict),
                        utils.format_metric(test_res_dict),
                        self.time[1] - self.time[0]))
                        
        # model.load_model() #???

    def evaluate(self, model, corpus, set_name):  # evaluate the results for an input set
        predictions, labels = self.predict(model, corpus, set_name)
        lengths = np.array(list(map(lambda lst: len(lst) - 1, corpus.data_df[set_name]['skill_seq'])))
        concat_pred, concat_label = list(), list()
        for pred, label, length in zip(predictions, labels, lengths):
            concat_pred.append(pred[:length])
            concat_label.append(label[:length])
        concat_pred = np.concatenate(concat_pred)
        concat_label = np.concatenate(concat_label)
        return model.module.pred_evaluate_method(concat_pred, concat_label, self.metrics)

    def print_res(self, model, corpus):
        set_name = 'test'
        result = self.evaluate(model, corpus, set_name)
        res_str = utils.format_metric(result)
        return res_str
