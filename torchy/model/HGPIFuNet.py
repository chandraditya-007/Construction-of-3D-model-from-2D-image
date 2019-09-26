import torch
import torch.nn as nn
import torch.nn.functional as F 
from .BasePIFuNet import BasePIFuNet
from .MLP import MLP
from .DepthNormalizer import DepthNormalizer
from .HGFilters import *
from .VolumetricEncoder import *
from ..net_util import init_net

class HGPIFuNet(BasePIFuNet):
    '''
    HGPIFu uses stacked hourglass as an image encoder.
    '''

    def __init__(self, 
                 opt, 
                 projection_mode='orthogonal',
                 criteria={'occ': nn.MSELoss()}
                 ):
        super(HGPIFuNet, self).__init__(
            projection_mode=projection_mode,
            criteria=criteria)

        self.name = 'hg_pifu'

        self.opt = opt
        self.num_views = self.opt.num_views
        self.image_filter = HGFilter(opt.num_stack, opt.hg_depth, opt.hg_dim, 
                                     opt.norm, opt.hg_down, False)

        self.mlp = MLP(
            filter_channels=self.opt.mlp_dim,
            num_views=self.num_views,
            res_layers=self.opt.mlp_res_layers,
            last_op=nn.Sigmoid())

        if self.opt.sp_enc_type == 'vol_enc':
            self.spatial_enc = VolumetricEncoder(opt)
        elif self.opt.sp_enc_type == 'z':
            self.spatial_enc = DepthNormalizer(opt)
        else:
            raise NameError('unknown spatial encoding type')

        self.im_feat_list = []
        self.tmpx = None
        self.normx = None

        self.intermediate_preds_list = []

        init_net(self)
    
    def filter(self, images):
        '''
        apply a fully convolutional network to images.
        the resulting feature will be stored.
        args:
            images: [B, C, H, W]
        '''
        if self.opt.sp_enc_type == 'vol_enc':
            self.spatial_enc.filter(images)

        self.im_feat_list, self.normx = self.image_filter(images)
        if not self.training:
            self.im_feat_list = [self.im_feat_list[-1]]
        
    def query(self, points, calibs, transforms=None, labels=None):
        '''
        given 3d points, we obtain 2d projection of these given the camera matrices.
        filter needs to be called beforehand.
        the prediction is stored to self.preds
        args:
            points: [B, 3, N] 3d points in world space
            calibs: [B, 3, 4] calibration matrices for each image
            transforms: [B, 2, 3] image space coordinate transforms
            labels: [B, C, N] ground truth labels (for supervision only)
        return:
            [B, C, N] prediction
        '''
        xyz = self.projection(points, calibs, transforms)
        xy = xyz[:, :2, :]
        
        # if the point is outside bounding box, return outside.
        in_bb = (xyz >= -1) & (xyz <= 1)
        in_bb = in_bb[:, 0, :] & in_bb[:, 1, :] & in_bb[:, 2, :]
        in_bb = in_bb[:, None, :].detach().float()

        if labels is not None:
            self.labels = in_bb * labels

        sp_feat = self.spatial_enc(xyz, calibs=calibs)

        self.intermediate_preds_list = []

        for i, im_feat in enumerate(self.im_feat_list):

            if self.opt.sp_enc_type == 'vol_enc' and self.opt.sp_no_pifu:
                point_local_feat = sp_feat
            else:
                point_local_feat_list = [self.index(im_feat, xy), sp_feat]            
                point_local_feat = torch.cat(point_local_feat_list, 1)
            pred = in_bb * self.mlp(point_local_feat)
            self.intermediate_preds_list.append(pred)
        
        self.preds = self.intermediate_preds_list[-1]

    def calc_normal(self, points, calibs, transforms=None, labels=None, delta=0.1, fd_type='forward'):
        '''
        return surface normal in 'model' space.
        it computes normal only in the last stack.
        note that the current implementation use forward difference.
        args:
            points: [B, 3, N] 3d points in world space
            calibs: [B, 3, 4] calibration matrices for each image
            transforms: [B, 2, 3] image space coordinate transforms
            delta: perturbation for finite difference
            fd_type: finite difference type (forward/backward/central) 
        '''
        pdx = points.clone()
        pdx[:,0,:] += delta
        pdy = points.clone()
        pdy[:,1,:] += delta
        pdz = points.clone()
        pdz[:,2,:] += delta

        if labels is not None:
            self.labels_nml = labels

        points_all = torch.stack([points, pdx, pdy, pdz], 3)
        points_all = points_all.view(*points.size()[:2],-1)
        xyz = self.projection(points_all, calibs, transforms)
        xy = xyz[:, :2, :]

        im_feat = self.im_feat_list[-1]
        sp_feat = self.spatial_enc(xyz, calibs=calibs)

        if self.opt.sp_enc_type == 'vol_enc' and self.opt.sp_no_pifu:
            point_local_feat = sp_feat
        else:
            point_local_feat_list = [self.index(im_feat, xy), sp_feat]            
            point_local_feat = torch.cat(point_local_feat_list, 1)
        pred = self.mlp(point_local_feat)

        pred = pred.view(*pred.size()[:2],-1,4) # (B, 1, N, 4)

        # divide by delta is omitted since it's normalized anyway
        dfdx = pred[:,:,:,1] - pred[:,:,:,0]
        dfdy = pred[:,:,:,2] - pred[:,:,:,0]
        dfdz = pred[:,:,:,3] - pred[:,:,:,0]

        nml = -torch.cat([dfdx,dfdy,dfdz], 1)
        nml = F.normalize(nml, dim=1, eps=1e-8)

        self.nmls = nml
        self.preds_surface = pred[:,:,:,0]

    def get_im_feat(self):
        '''
        return the image filter in the last stack
        return:
            [B, C, H, W]
        '''
        return self.im_feat_list[-1]

    def get_error(self):
        '''
        return the loss given the ground truth labels and prediction
        '''
        error = {}
        error['Err(occ)'] = 0
        for preds in self.intermediate_preds_list:
            error['Err(occ)'] += self.criteria['occ'](preds, self.labels)
        
        error['Err(occ)'] /= len(self.intermediate_preds_list)
        
        if self.nmls is not None and self.labels_nml is not None:
            error['Err(nml)'] = self.criteria['nml'](self.nmls, self.labels_nml)
            error['Err(occ)'] += self.criteria['occ'](self.preds_surface, 0.5*torch.ones_like(self.preds_surface))
        
        return error

    def forward(self, images, points, calibs, labels, points_nml=None, labels_nml=None):
        self.filter(images)
        self.query(points, calibs, labels=labels)
        if points_nml is not None and labels_nml is not None:
            self.calc_normal(points_nml, calibs, labels=labels_nml)
        res = self.get_preds()
            
        err = self.get_error()

        return err, res
