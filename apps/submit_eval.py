# import submitit
from .train import trainerWrapper
from .recon_eval import reconWrapper
from .recon_mr import reconWrapper as reconWrapperMR
from .recon_mr_eval import reconWrapper as reconWrapperMREval

# executor = submitit.AutoExecutor(folder="cluster_log")  # submission interface (logs are dumped in the folder)
# executor.update_parameters(timeout_min=2*60, gpus_per_node=4, cpus_per_task=40, partition="dev", name='wildPIFu', comment='cvpr deadline')  # timeout in min

###############################################################################################
##                   Upper PIFu
###############################################################################################

resolution = '512'

file_size = 1490 # set the total size of inputs (if split_size is 1, you can ignore)
split_size = 1 # set how many jobs you want to launch
interval = file_size // split_size

for i in range(split_size+1):
       if split_size < 2:
              start_id = -1
              end_id = -1
       else:
              start_id = i * interval
              end_id = (i+1) * interval
       cmd = ['--dataroot', '/home/hjoo/codes/wildpifu/test/', '--results_path', '.',\
              '--loadSize', '1024', '--resolution', resolution, '--load_netMR_checkpoint_path', \
              '/run/media/hjoo/disk/data/pifuhd/checkpoints/ours_final_train_latest',\
              '--start_id', '%d' % start_id, '--end_id', '%d' % end_id]
       reconWrapperMR(cmd)
       # job = executor.submit(reconWrapperMR, cmd)  
       # print(job.job_id)  # ID of your job


###############################################################################################
##                  Ablation Study
###############################################################################################

# model_names = ['lower_pifu',
#                'resnet']

# resolution = '512'

# for model in model_names:
#     cmd = ['--dataroot', './../../data/eval_dataset/RP', '--results_path', './../../data/eval_dataset/results_512',\
#            '--resolution', resolution, '--load_netG_checkpoint_path', \
#            './checkpoints/%s_train_latest' % model]
#     job = executor.submit(reconWrapper, cmd)  
#     print(job.job_id)  # ID of your job

#     cmd = ['--dataroot', './../../data/eval_dataset/BUFF', '--results_path', './../../data/eval_dataset/results_512',\
#            '--resolution', resolution, '--load_netG_checkpoint_path', \
#            './checkpoints/%s_train_latest' % model]
#     job = executor.submit(reconWrapper, cmd)  
#     print(job.job_id)  # ID of your job

# model_names = ['ours_final',
#               'ours_end2end',
#               'ours_fixed_nonml',
#               'upperonly_noglobal_fullres'
#               'upperonly_wglobal_fullres',
#               'upperonly_noglobal_window'
#               'upperonly_wglobal_window']

# resolution = '256'

# for model in model_names:
#     cmd = ['--dataroot', './../../data/eval_dataset/RP', '--results_path', './../../data/eval_dataset/results',\
#            '--loadSize', '1024', '--resolution', resolution, '--load_netMR_checkpoint_path', \
#            './checkpoints/%s_train_latest' % model]
#     job = executor.submit(reconWrapperMREval, cmd)  
#     print(job.job_id)  # ID of your job

#     cmd = ['--dataroot', './../../data/eval_dataset/BUFF', '--results_path', './../../data/eval_dataset/results',\
#            '--loadSize', '1024', '--resolution', resolution, '--load_netMR_checkpoint_path', \
#            './checkpoints/%s_train_latest' % model]
#     job = executor.submit(reconWrapperMREval, cmd)  
#     print(job.job_id)  # ID of your job