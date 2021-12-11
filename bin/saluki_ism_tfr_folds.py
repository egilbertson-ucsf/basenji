#!/usr/bin/env python
# Copyright 2020 Calico LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =========================================================================

from optparse import OptionParser, OptionGroup
import glob
import os
import pickle
import pdb
import shutil
import subprocess
import sys

import h5py
import numpy as np
import pandas as pd

import slurm

"""
saluki_bench_gtex.py

Compute SNP expression difference scores for variants in VCF files of
fine-mapped GTEx variants to benchmark as features in a classification
task.
"""

################################################################################
# main
################################################################################
def main():
  usage = 'usage: %prog [options] <models_dir>'
  parser = OptionParser(usage)

  # ism-tfr
  ism_options = OptionGroup(parser, 'saluki_ism_tfr.py options')
  ism_options.add_option('-l', dest='mut_len',
      default=None, type='int',
      help='Length of 3\' sequence to mutate [Default: %default]')
  ism_options.add_option('-o', dest='out_dir',
      default='ism',
      help='Output directory for ISM [Default: %default]')
  ism_options.add_option('--split', dest='split_label',
      default='test',
      help='Dataset split label for eg TFR pattern [Default: %default]')
  parser.add_option_group(ism_options)

  # multi
  fold_options = OptionGroup(parser, 'cross-fold options')
  fold_options.add_option('-d', dest='data_head',
      default=None, type='int',
      help='Index for dataset/head [Default: %default]')
  fold_options.add_option('--data', dest='data_dir',
      default=None,
      help='Data directory for processing TFRecords in proper order [Default: %default]')
  fold_options.add_option('-e', dest='conda_env',
      default='tf2.6-rna',
      help='Anaconda environment [Default: %default]')
  fold_options.add_option('--name', dest='name',
      default='ism', help='SLURM name prefix [Default: %default]')
  fold_options.add_option('--max_proc', dest='max_proc',
      default=None, type='int',
      help='Maximum concurrent processes [Default: %default]')
  fold_options.add_option('-p', dest='processes',
      default=None, type='int',
      help='Number of processes, passed by multi script. \
            (Unused, but needs to appear as dummy.)')
  fold_options.add_option('-q', dest='queue',
      default='gtx1080ti',
      help='SLURM queue on which to run the jobs [Default: %default]')
  fold_options.add_option('-r', dest='restart',
      default=False, action='store_true',
      help='Restart a partially completed job [Default: %default]')
  parser.add_option_group(fold_options)

  (options, args) = parser.parse_args()

  if len(args) != 1:
    parser.error('Must provide cross-validation model directory')
  else:
    models_dir = args[0]

  #######################################################
  # prep work

  model_str = 'model_best.h5'
  if options.data_head is not None:
    model_str = 'model%d_best.h5' % options.data_head

  num_folds = len(glob.glob('%s/f*_c0/train/%s' % (models_dir,model_str)))
  num_crosses = len(glob.glob('%s/f0_c*/train/%s' % (models_dir,model_str)))
  print('Folds %d, Crosses %d' % (num_folds, num_crosses))

  #######################################################
  # predict

  params_file = '%s/params.json' % models_dir

  cmd_base = '. /home/drk/anaconda3/etc/profile.d/conda.sh;'
  cmd_base += ' conda activate %s;' % options.conda_env
  cmd_base += ' saluki_ism_tfr.py %s' % params_file

  jobs = []
  for fi in range(num_folds):
    for ci in range(num_crosses):
      fc = 'f%d_c%d' % (fi, ci)
      model_file = '%s/%s/train/%s' % (models_dir, fc, model_str)
      data_dir = '%s/%s/data0' % (models_dir, fc)
      out_dir = '%s/%s/%s' % (models_dir, fc, options.out_dir)

      if options.split_label == '*':
        data_dir = options.data_dir

      if not options.restart or not os.path.isfile('%s/scores.h5'%out_dir):
        cmd = '%s %s %s' % (cmd_base, model_file, data_dir)
        cmd += ' %s' % options_string(options, ism_options, out_dir)
        j = slurm.Job(cmd, '%s_%s' % (options.name, fc),
            '%s.out'%out_dir, '%s.err'%out_dir,
            queue=options.queue, gpu=1,
            mem=30000, time='2-0:0:0')
        jobs.append(j)

  slurm.multi_run(jobs, max_proc=options.max_proc, verbose=True,
                  launch_sleep=10, update_sleep=60)

  #######################################################
  # ensemble

  # ensemble_dir = '%s/ensemble' % models_dir
  # os.makedirs(ensemble_dir, exist_ok=True)

  # if split_label == '*':
  #   ensemble_all(models_dir, options.out_dir, num_folds, num_crosses)
  # else:
  #   ensemble_folds(models_dir, options.out_dir, num_folds, num_crosses)


"""
# this is wrong because it writes one ensemble rather than one per fold.
def ensemble_folds(models_dir, out_dir, num_folds, num_crosses):
  for fi in range(num_folds):
    print('Ensembling fold %d' % fi)
    scores_c0_file = '%s/f%d_c0/%s/scores.h5' % (models_dir, fi, out_dir)
    scores_ens_dir = '%s/ensemble/%s' % (models_dir, out_dir)
    scores_ens_file = '%s/scores.h5' % scores_ens_dir

    # copy cross0
    os.makedirs(scores_ens_dir, exist_ok=True)
    shutil.copy(scores_c0_file, scores_ens_file)

    # read ism scores
    cross_ism = []
    for ci in range(num_crosses):
      scores_ci_file = '%s/f%d_c%d/%s/scores.h5' % (models_dir, fi, ci, out_dir)
      with h5py.File(scores_ci_file, 'r') as scores_ci_h5:
        cross_ism.append(scores_ci_h5['ism'][:])

    # take average
    cross_ism = np.array(cross_ism, dtype='float32')
    avg_ism = cross_ism.mean(axis=0).astype('float16')

    # write to ensemble
    with h5py.File(scores_ens_file, 'a') as scores_ens_h5:
      scores_ens_h5['ism'][:] = avg_ism
"""

def options_string(options, ism_options, out_dir):
  options_str = ''

  for opt in ism_options.option_list:
    opt_str = opt.get_opt_string()
    opt_value = options.__dict__[opt.dest]

    # wrap askeriks in ""
    if type(opt_value) == str and opt_value.find('*') != -1:
      opt_value = '"%s"' % opt_value

    # no value for bools
    elif type(opt_value) == bool:
      if not opt_value:
        opt_str = ''
      opt_value = ''

    # skip Nones
    elif opt_value is None:
      opt_str = ''
      opt_value = ''

    # modify
    elif opt.dest == 'out_dir':
      opt_value = out_dir

    options_str += ' %s %s' % (opt_str, opt_value)

  return options_str

################################################################################
# __main__
################################################################################
if __name__ == '__main__':
  main()