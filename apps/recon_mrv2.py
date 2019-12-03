import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
ROOT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

import time
import json 
import numpy as np
import cv2
import random
import torch
import torch.nn as nn
from tqdm import tqdm
from torch.utils.data import DataLoader
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib
from numpy.linalg import inv

from lib.options import BaseOptions
from lib.visualizer import Visualizer
from lib.mesh_util import *
from lib.sample_util import *
from torchy.data import *
from torchy.model import *
from torchy.geometry import index

from PIL import Image
import torchvision.transforms as transforms

parser = BaseOptions()

def label_to_color(nways, N=10):
    '''
    args:
        nwaysL [N] integer numpy array
    return:
        [N, 3] color numpy array
    '''
    mapper = cm.ScalarMappable(norm=matplotlib.colors.Normalize(vmin=0, vmax=N), cmap=plt.get_cmap('tab10'))

    colors = []
    for v in nways.tolist():
        colors.append(mapper.to_rgba(float(v)))

    return np.stack(colors, 0)[:,:3]

def reshape_multiview_tensors(image_tensor, calib_tensor):
    '''
    args:
        image_tensor: [B, nV, C, H, W]
        calib_tensor: [B, nV, 3, 4]
    return:
        image_tensor: [B*nV, C, H, W]
        calib_tensor: [B*nV, 3, 4]
    '''
    image_tensor = image_tensor.view(
        image_tensor.shape[0] * image_tensor.shape[1],
        image_tensor.shape[2],
        image_tensor.shape[3],
        image_tensor.shape[4]
    )
    calib_tensor = calib_tensor.view(
        calib_tensor.shape[0] * calib_tensor.shape[1],
        calib_tensor.shape[2],
        calib_tensor.shape[3]
    )

    return image_tensor, calib_tensor

def reshape_sample_tensor(sample_tensor, num_views):
    '''
    args:
        sample_tensor: [B, 3, N] xyz coordinates
        num_views: number of views
    return:
        [B*nV, 3, N] repeated xyz coordinates
    '''
    if num_views == 1:
        return sample_tensor
    sample_tensor = sample_tensor[:, None].repeat(1, num_views, 1, 1)
    sample_tensor = sample_tensor.view(
        sample_tensor.shape[0] * sample_tensor.shape[1],
        sample_tensor.shape[2],
        sample_tensor.shape[3]
    )
    return sample_tensor

def gen_mesh(res, net, cuda, data, save_path, thresh=0.5, use_octree=True, components=False):
    image_tensor_global = data['img_512'].to(device=cuda)
    image_tensor = data['img'].to(device=cuda)
    calib_tensor = data['calib'].to(device=cuda)

    net.filter_global(image_tensor_global)
    net.filter_local(image_tensor[:,None])

    try:
        if net.netG.netF is not None:
            image_tensor_global = torch.cat([image_tensor_global, net.netG.nmlF], 0)
        if net.netG.netB is not None:
            image_tensor_global = torch.cat([image_tensor_global, net.netG.nmlB], 0)
    except:
        pass
    
    b_min = data['b_min']
    b_max = data['b_max']
    try:
        save_img_path = save_path[:-4] + '.png'
        save_img_list = []
        for v in range(image_tensor_global.shape[0]):
            save_img = (np.transpose(image_tensor_global[v].detach().cpu().numpy(), (1, 2, 0)) * 0.5 + 0.5)[:, :, ::-1] * 255.0
            save_img_list.append(save_img)
        save_img = np.concatenate(save_img_list, axis=1)
        cv2.imwrite(save_img_path, save_img)

        verts, faces, _, _ = reconstruction(
            net, cuda, calib_tensor, res, b_min, b_max, thresh, use_octree=use_octree, num_samples=100000)
        verts_tensor = torch.from_numpy(verts.T).unsqueeze(0).to(device=cuda).float()
        if 'calib_world' in data:
            calib_world = data['calib_world'].numpy()[0]
            verts = np.matmul(np.concatenate([verts, np.ones_like(verts[:,:1])],1), inv(calib_world).T)[:,:3]

        color = np.zeros(verts.shape)
        interval = 100000
        for i in range(len(color) // interval + 1):
            left = i * interval
            if i == len(color) // interval:
                right = -1
            else:
                right = (i + 1) * interval
            net.calc_normal(verts_tensor[:, None, :, left:right], calib_tensor[:,None], calib_tensor)
            nml = net.nmls.detach().cpu().numpy()[0] * 0.5 + 0.5
            color[left:right] = nml.T

        save_obj_mesh_with_color(save_path, verts, faces, color)
    except Exception as e:
        print(e)


def recon(opt):
    # load checkpoints
    state_dict_path = None
    if opt.load_netMR_checkpoint_path is not None:
        state_dict_path = opt.load_netMR_checkpoint_path
    elif opt.resume_epoch < 0:
        state_dict_path = '%s/%s_train_latest' % (opt.checkpoints_path, opt.name)
        opt.resume_epoch = 0
    else:
        state_dict_path = '%s/%s_train_epoch_%d' % (opt.checkpoints_path, opt.name, opt.resume_epoch)
    
    start_id = opt.start_id
    end_id = opt.end_id

    state_dict = None
    if state_dict_path is not None and os.path.exists(state_dict_path):
        print('Resuming from ', state_dict_path)
        state_dict = torch.load(state_dict_path)    
        if 'opt' in state_dict:
            print('Warning: opt is overwritten.')
            dataroot = opt.dataroot
            resolution = opt.resolution
            results_path = opt.results_path
            loadSize = opt.loadSize
            
            opt = state_dict['opt']
            opt.dataroot = dataroot
            opt.resolution = resolution
            opt.results_path = results_path
            opt.loadSize = loadSize
    else:
        raise Exception('failed loading state dict!', state_dict_path)
    
    parser.print_options(opt)

    cuda = torch.device('cuda:%d' % opt.gpu_id)

    # test_dataset = EvalDataset(opt)
    test_dataset = EvalWPoseDataset(opt)

    print('test data size: ', len(test_dataset))
    projection_mode = test_dataset.projection_mode

    opt_netG = state_dict['opt_netG']
    netG = HGPIFuNetwNML(opt_netG, projection_mode).to(device=cuda)

    if 'hg_ablation' in opt.netG:
        netMR = HGPIFuMRNetAblation(opt, projection_mode)
    elif 'resblk_ablation' in opt.netG:
        netMR = ResBlkPIFuMRNetAblation(opt, projection_mode)
    elif 'hg' in opt.netG:
        netMR = HGPIFuMRNetV2(opt, netG, projection_mode)
    elif 'resblk' in opt.netG:
        netMR = ResBlkPIFuMRNet(opt, netG, projection_mode)

    netMR = netMR.to(device=cuda)

    def set_eval():
        netG.eval()

    # load checkpoints
    if state_dict is not None:
        if 'model_state_dict' in state_dict:
            netMR.load_state_dict(state_dict['model_state_dict'])
        else: # this is deprecated but keep it for now.
            netMR.load_state_dict(state_dict)

    os.makedirs(opt.checkpoints_path, exist_ok=True)
    os.makedirs(opt.results_path, exist_ok=True)
    os.makedirs('%s/%s/recon' % (opt.results_path, opt.name), exist_ok=True)

    if start_id < 0:
        start_id = 0
    if end_id < 0:
        end_id = len(test_dataset)

    ## test
    with torch.no_grad():
        set_eval()

        print('generate mesh (test) ...')
        for i in tqdm(range(start_id, end_id)):
            if i >= len(test_dataset):
                break
            
            # for multi-person processing, set it to False
            if True:
                test_data = test_dataset[i]
                save_path = '%s/%s/recon/result_%s.obj' % (opt.results_path, opt.name, test_data['name'])
                gen_mesh(opt.resolution, netMR, cuda, test_data, save_path, components=opt.use_compose)
            else:
                for j in range(test_dataset.get_n_person(i)):
                    test_dataset.person_id = j
                    test_data = test_dataset[i]
                    save_path = '%s/%s/recon/result_%s_%d.obj' % (opt.results_path, opt.name, test_data['name'], j)
                    gen_mesh(opt.resolution, netMR, cuda, test_data, save_path, components=opt.use_compose)

def reconWrapper(args=None):
    opt = parser.parse(args)
    recon(opt)

if __name__ == '__main__':
    reconWrapper()
  
