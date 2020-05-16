import torch
from torch.utils.data import DataLoader, IterableDataset
from loguru import logger
from glob import glob 
import numpy as np
from tqdm import tqdm
from itertools import chain 


class PUDataset(IterableDataset):
    def __init__(self, config):
        self._files = list(chain.from_iterable(
                [glob(pattern)
                    for pattern in config.dataset_pattern]
            ))
        self.num_max_files = config.num_max_files
        self.mask_charged = config.mask_charged
        self.n_particles = config.num_max_particles
        self.dr_adj = config.dr_adj
        if hasattr(config, 'min_met'):
            self.min_met = config.min_met 
        else:
            self.min_met = None 
        self._len = self._get_len() 

    def __len__(self):
        return self._len

    def _get_len(self):
        n_tot = 0
        np.random.shuffle(self._files)
        for f in self._files[:self.num_max_files]:
            data = np.load(f)
            N = data['N']
            if self.min_met is not None:
                genmet = data['met']
                evt_mask = genmet > self.min_met
            else:
                evt_mask = np .ones_like(N).astype(bool)
            n_tot += N[evt_mask].shape[0]
        return n_tot

    @staticmethod
    def cone_adj(eta, phi, cone=0.4):
        if cone == 0.0:
            N = eta.shape[0]
            return np.eye(N, N)
        deta2 = np.square(eta[:,np.newaxis] - eta)
        dphi2 = np.square(phi[:,np.newaxis] - phi)
        dr2 = deta2 + dphi2 
        cone2 = cone ** 2
        adj = (dr2 <= cone2)
        return adj 

    def __iter__(self):
        np.random.shuffle(self._files)
        for f in self._files[:self.num_max_files]:
            data = np.load(f)
            X = data['x']
            Y = data['y']
            N = data['N']
            P = data['p']
            Q = data['q']
            genmet = data['met']
            genmetphi = data['metphi']
            mjj = data['mjj']
            jpt0 = data['jpt0']
            jm0 = data['jm0']
            puppimet = data['puppimet']
            pfmet = data['pfmet']

            X = X[:, :self.n_particles, :]
            Y = Y[:, :self.n_particles]
            P = P[:, :self.n_particles]
            Q = Q[:, :self.n_particles]

            mask_base = np.arange(X.shape[1])
            idx = np.arange(X.shape[0])
            np.random.shuffle(idx)
            for i in idx:
                mask = (mask_base < N[i]).astype(int) 
                x = X[i, :, :]
                if self.dr_adj is not None:
                    adj = self.cone_adj(x[:, 1], x[:, 2], self.dr_adj)
                    adj_mask = np.logical_and(
                            adj,
                            np.logical_and(mask[:, None], mask)
                        )
                else:
                    adj_mask = mask 
                adj_mask = adj_mask.astype(int)
                y = Y[i, :].astype(int)
                y[~mask] = -1
                orig_y = np.copy(y)
                if self.mask_charged:
                    q_mask = (x[:, 5] != 0)
                    y[q_mask] = -1
                to_yield = {
                        'x': x,
                        'y': y,
                        'adj_mask': adj_mask,
                        'q': Q[i, :],
                        'p': P[i, :],
                        'genmet': genmet[i],
                        'genmetphi': genmetphi[i],
                        'puppimet': puppimet[i],
                        'pfmet': pfmet[i],
                        'orig_y': orig_y,
                        'mask': mask,
                        'mjj': mjj[i],
                        'jpt0': jpt0[i],
                        'jm0': jm0[i]
                    }
                # to_yield = (x, y, adj_mask)
                # to_yield += (Q[i, :], P[i, :], genmet[i], pfmet[i], orig_y, mask)
                yield to_yield

    @staticmethod
    def collate_fn(samples):
        keys = list(samples[0].keys())
        to_ret = {k:np.stack([s[k] for s in samples], axis=0) for k in keys}
        return to_ret 

        # n_fts = len(samples[0])
        # to_ret = [np.stack([s[i] for s in samples], axis=0) for i in range(n_fts)]
        # return to_ret


class METDataset(IterableDataset):
    def __init__(self, dataset_pattern, config, mean_met, std_met, training_mode):
        self._files = sorted(glob(dataset_pattern))[:config.num_max_files]
        self.mask_charged = config.mask_charged
        self.dr_adj = config.dr_adj
        self._len = self._get_len() 
        self.mean_met, self.std_met = mean_met, std_met
        self.training_mode = training_mode

    def __len__(self):
        return self._len

    def _get_len(self):
        n_tot = 0
        for f in self._files:
            data = np.load(f)
            N = data['N']
            n_tot += N.shape[0]
        return n_tot

    @staticmethod
    def cone_adj(eta, phi, cone=0.4):
        deta2 = np.square(eta[:,np.newaxis] - eta)
        dphi2 = np.square(phi[:,np.newaxis] - phi)
        dr2 = deta2 + dphi2 
        cone2 = cone ** 2
        # adj = (dr2 <= cone2)
        adj = dr2 == 0
        return adj 

    def standardize_met(self, met):
        return (met - self.mean_met) / self.std_met

    def unstandardize_met(self, met):
        return (self.std_met * met) + self.mean_met

    '''
    @staticmethod
    def _compute_met(pt, phi):
        px = pt * np.cos(phi)
        py = pt * np.sin(phi)
        metx = np.sum(px, axis=-1)
        return np.abs(metx)
        mety = np.sum(py, axis=-1)
        met = np.sqrt(np.power(metx, 2) + np.power(mety, 2))
        return met
    '''
    @staticmethod
    def _compute_met(px, py):
        metx = np.sum(px, axis=-1)
        # return np.abs(metx)
        mety = np.sum(py, axis=-1)
        met = np.sqrt(np.power(metx, 2) + np.power(mety, 2))
        return met

    @staticmethod
    def _transform_x(x):
        # starts with [pt, eta, phi, other stuff]
        # becomes     [px, eta, py, other stuff] 
        pt, eta, phi = x[:,:,0], x[:,:,1], x[:,:,2]
        px = pt * np.cos(phi)
        py = pt * np.sin(phi)
        x[:,:,0] = px
        x[:,:,2] = py
        return x

    def __iter__(self):
        np.random.shuffle(self._files)
        for f in self._files:
            data = np.load(f)
            X = data['x']
            N = data['N']
            genmet = data['met']
            pfmet = data['pfmet']

            # calcmet = np.sum(X[:, :20, 0] * X[:, :20, 2], axis=-1)

            X = self._transform_x(X)
            if self.training_mode == 0:
                X = X[:,:50,:]
            elif self.training_mode == 1:
                X = X[:,:200,:]
            else:
                X = X[:,:200,:]
            calcmet = self._compute_met(X[:, :, 0], X[:, :, 2])

            mask_base = np.arange(X.shape[1])
            idx = np.arange(X.shape[0])
            #idx = idx[np.logical_and(genmet > 0, genmet < 40)]
            np.random.shuffle(idx)
            
            if self.mean_met is not None:
                genmet = self.standardize_met(genmet)
                calcmet = self.standardize_met(calcmet)
                pfmet = self.standardize_met(pfmet)

            for i in idx:
                mask = (mask_base < N[i])
                x = X[i, :, :]
                if self.dr_adj is not None:
                    # adj = self.cone_adj(x[:, 1], x[:, 2], self.dr_adj)
                    adj = np.eye(x.shape[0])
                    adj_mask = np.logical_and(adj, np.logical_and(mask[:, np.newaxis], mask))
                else:
                    adj_mask = mask 
                adj_mask = adj_mask.astype(int)
                if self.training_mode in {0, 1}:
                    to_yield = (x, adj_mask, calcmet[i], pfmet[i])
                # elif self.training_mode == 1:
                #     to_yield = (x, adj_mask, pfmet[i], pfmet[i])
                else:
                    to_yield = (x, adj_mask, genmet[i], pfmet[i])
                #to_yield = (x, adj_mask, self.standardize_met(x[0,0]) , pfmet[i])
                yield to_yield

    @staticmethod
    def collate_fn(samples):
        n_fts = len(samples[0])
        to_ret = [np.stack([s[i] for s in samples], axis=0) for i in range(n_fts)]
        return to_ret