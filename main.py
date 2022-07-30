# -*- coding: UTF-8 -*-

import os
import sys
import time
import pickle
import logging
import argparse
import numpy as np
import torch

from models import *
from helpers import *
from utils import utils

import ipdb

def parse_global_args(parser):
    parser.add_argument('--gpu', type=str, default='0',
                        help='Set CUDA_VISIBLE_DEVICES')
    parser.add_argument('--verbose', type=int, default=logging.INFO,
                        help='Logging Level, 0, 10, ..., 50')
    parser.add_argument('--log_file', type=str, default='',
                        help='Logging file path')
    parser.add_argument('--random_seed', type=int, default=2022,
                        help='Random seed of numpy and pytorch.')
    parser.add_argument('--load', type=int, default=1,
                        help='Whether load model and continue to train')
    parser.add_argument('--fold', type=int, default=0,
                        help='Select a fold to run.')
    parser.add_argument('--train', type=int, default=1,
                        help='To train the model or not.')
    parser.add_argument('--regenerate', type=int, default=0,
                        help='Whether to regenerate intermediate files.')
    return parser


def main(args):
    # logging.info(msg, *args, **kwargs) Logs a message with level INFO on the root logger. 
    logging.info('-' * 45 + ' BEGIN: ' + utils.get_time() + ' ' + '-' * 45)
    exclude = ['check_epoch', 'log_file', 'model_path', 'path', 'pin_memory',
               'regenerate', 'sep', 'train', 'verbose']
    logging.info(utils.format_arg_str(args, exclude_lst=exclude))

    # Random seed
    torch.manual_seed(args.random_seed)
    torch.cuda.manual_seed(args.random_seed)
    np.random.seed(args.random_seed)

    # GPU
    os.environ["CUDA_VISIBLE_DEVICES"] = args.gpu
    logging.info("# cuda devices: {}".format(torch.cuda.device_count()))

    # Load data
    corpus = load_corpus(args) #junyi train 178276 dev 19808 test 49522

    # Define model
    model = model_name(args, corpus)
    logging.info(model)
    model = model.double()
    model.apply(model.init_weights)
    model.actions_before_train()
    if torch.cuda.device_count() > 0:
        model = model.cuda()

    runner = runner_name(args)

    logging.info('Test Before Training: ' + runner.print_res(model, corpus))
    if args.load > 0:
        model.load_model()
        
    if args.train > 0:
        runner.train(model, corpus)
    logging.info('\nTest After Training: ' + runner.print_res(model, corpus))

    model.actions_after_train()
    logging.info(os.linesep + '-' * 45 + ' END: ' + utils.get_time() + ' ' + '-' * 45)


def load_corpus(args):
    corpus_path = os.path.join(args.path, args.dataset, 'Corpus_{}.pkl'.format(args.max_step))
    if not args.regenerate and os.path.exists(corpus_path):
        logging.info('Load corpus from {}'.format(corpus_path))
        with open(corpus_path, 'rb') as f:
            corpus = pickle.load(f)
    else:
        t1 = time.time()
        corpus = reader_name(args)
        logging.info('Done! [{:<.2f} s]'.format(time.time() - t1))
        logging.info('Save corpus to {}'.format(corpus_path))
        with open(corpus_path, 'wb') as f:
            pickle.dump(corpus, f)
    corpus.gen_fold_data(args.fold)
    return corpus


if __name__ == '__main__':
    init_parser = argparse.ArgumentParser(description='Model')
    init_parser.add_argument('--model_name', type=str, default='hkt', help='Choose a model to run.')
    init_args, init_extras = init_parser.parse_known_args()
    model_name = eval('{0}.{0}'.format(init_args.model_name)) # hkt file
    reader_name = eval('{0}.{0}'.format(model_name.reader)) # DataReader file
    runner_name = eval('{0}.{0}'.format(model_name.runner)) # KTRunner file

    # Args
    parser = argparse.ArgumentParser(description='')
    parser = parse_global_args(parser)
    parser = reader_name.parse_data_args(parser)
    parser = runner_name.parse_runner_args(parser)
    parser = model_name.parse_model_args(parser)
    args, extras = parser.parse_known_args() # https://docs.python.org/3/library/argparse.html?highlight=parse_known_args#argparse.ArgumentParser.parse_known_args

    # Logging configuration
    log_args = [init_args.model_name, args.dataset, str(args.random_seed)]
    for arg in ['lr', 'l2', 'fold'] + model_name.extra_log_args:
        log_args.append(arg + '=' + str(eval('args.' + arg))) 
        # ['hkt', 'test', '2019', 'lr=0.005', 'l2=1e-05', 'fold=0', 'time_log=5.0']
    log_file_name = '__'.join(log_args).replace(' ', '__')

    if args.log_file == '':
        args.log_file = args.path + '/log/{}/{}.txt'.format(init_args.model_name, log_file_name)
    if args.model_path == '':
        args.model_path = args.path + '/model/{}/{}.pt'.format(init_args.model_name, log_file_name)

    utils.check_dir(args.log_file)
    logging.basicConfig(filename=args.log_file, level=args.verbose)
    logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
    logging.info(init_args)

    main(args)
