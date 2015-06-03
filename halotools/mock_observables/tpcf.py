

"""
functions to calculate two point correlation functions
"""

from __future__ import division, print_function

__all__=['tpcf']

####import modules########################################################################
import sys
import numpy as np
from math import pi, gamma
from .pair_counters.grid_pairs import npairs, xy_z_npairs
##########################################################################################

def tpcf(sample1, rbins, sample2 = None, randoms=None, 
         period = None, max_sample_size=int(1e6),
         do_auto=True, do_cross=True, estimator='Natural', 
         N_threads=1, comm=None):
    """ Calculate the two-point correlation function. 
    
    Parameters 
    ----------
    sample1 : array_like
        Npts x k numpy array containing k-d positions of Npts. 
    
    rbins : array_like
        numpy array of boundaries defining the bins in which pairs are counted. 
        len(rbins) = Nrbins + 1.
    
    sample2 : array_like, optional
        Npts x k numpy array containing k-d positions of Npts.
    
    randoms : array_like, optional
        Nran x k numpy array containing k-d positions of Npts.
    
    period: array_like, optional
        length k array defining axis-aligned periodic boundary conditions. If only 
        one number, Lbox, is specified, period is assumed to be np.array([Lbox]*k).
        If none, PBCs are set to infinity.
    
    max_sample_size : int, optional
        Defines maximum size of the sample that will be passed to the pair counter. 
        
        If sample size exeeds max_sample_size, the sample will be randomly down-sampled 
        such that the subsamples are (roughly) equal to max_sample_size. 
        Subsamples will be passed to the pair counter in a simple loop, 
        and the correlation function will be estimated from the median pair counts in each bin.
    
    estimator: string, optional
        options: 'Natural', 'Davis-Peebles', 'Hewett' , 'Hamilton', 'Landy-Szalay'
    
    N_thread: int, optional
        number of threads to use in calculation.
    
    comm: mpi Intracommunicator object, optional
    
    do_auto: boolean, optional
        do auto-correlation?
    
    do_cross: boolean, optional
        do cross-correlation?

    Returns 
    -------
    correlation_function : array_like
        array containing correlation function :math:`\\xi` computed in each of the Nrbins 
        defined by input `rbins`.

        :math:`1 + \\xi(r) \equiv DD / RR`, 
        where `DD` is calculated by the pair counter, and RR is counted by the internally 
        defined `randoms` if no randoms are passed as an argument.

        If sample2 is passed as input, three arrays of length Nrbins are returned: two for
        each of the auto-correlation functions, and one for the cross-correlation function. 

    """
    
    def list_estimators(): #I would like to make this accessible from the outside. Know how?
        estimators = ['Natural', 'Davis-Peebles', 'Hewett' , 'Hamilton', 'Landy-Szalay']
        return estimators
    estimators = list_estimators()
    
    #process input parameters
    sample1 = np.asarray(sample1)
    if sample2 is not None: sample2 = np.asarray(sample2)
    else: sample2 = sample1
    if randoms is not None: randoms = np.asarray(randoms)
    rbins = np.asarray(rbins)
    #Process period entry and check for consistency.
    if period is None:
            PBCs = False
            period = np.array([np.inf]*np.shape(sample1)[-1])
    else:
        PBCs = True
        period = np.asarray(period).astype("float64")
        if np.shape(period) == ():
            period = np.array([period]*np.shape(sample1)[-1])
        elif np.shape(period)[0] != np.shape(sample1)[-1]:
            raise ValueError("period should have shape (k,)")
            return None
    #down sample is sample size exceeds max_sample_size.
    if (len(sample2)>max_sample_size) & (not np.all(sample1==sample2)):
        inds = np.arange(0,len(sample2))
        np.random.shuffle(inds)
        inds = inds[0:max_sample_size]
        sample2 = sample2[inds]
        print('down sampling sample2...')
    if len(sample1)>max_sample_size:
        inds = np.arange(0,len(sample1))
        np.random.shuffle(inds)
        inds = inds[0:max_sample_size]
        sample1 = sample1[inds]
        print('down sampling sample1...')
    
    if np.shape(rbins) == ():
        rbins = np.array([rbins])
    
    k = np.shape(sample1)[-1] #dimensionality of data
    
    #check for input parameter consistency
    if (period is not None) & (np.max(rbins)>np.min(period)/2.0):
        raise ValueError('Cannot calculate for seperations larger than Lbox/2.')
    if (sample2 is not None) & (sample1.shape[-1]!=sample2.shape[-1]):
        raise ValueError('Sample 1 and sample 2 must have same dimension.')
    if (randoms is None) & (min(period)==np.inf):
        raise ValueError('If no PBCs are specified, randoms must be provided.')
    if estimator not in estimators: 
        raise ValueError('Must specify a supported estimator. Supported estimators are:{0}'
        .value(estimators))
    if (PBCs==True) & (max(period)==np.inf):
        raise ValueError('If a non-infinte PBC specified, all PBCs must be non-infinte.')

    #If PBCs are defined, calculate the randoms analytically. Else, the user must specify 
    #randoms and the pair counts are calculated the old fashion way.
    def random_counts(sample1, sample2, randoms, rbins, period, PBCs, k, N_threads, do_RR, do_DR, comm):
        """
        Count random pairs.  There are three high level branches: 
            1. no PBCs w/ randoms.
            2. PBCs w/ randoms
            3. PBCs and analytical randoms
        Within each of those branches there are 3 branches to use:
            1. MPI
            2. no threads
            3. threads
        There are also logical bits to do RR and DR pair counts, as not all estimators 
        need one or the other, and not doing these can save a lot of calculation.
        """
        def nball_volume(R,k):
            """
            Calculate the volume of a n-shpere.  This is used for the analytical randoms.
            """
            return (pi**(k/2.0)/gamma(k/2.0+1.0))*R**k
        
        #No PBCs, randoms must have been provided.
        if PBCs==False:
            RR = npairs(randoms, randoms, rbins, period=period)
            RR = np.diff(RR)
            D1R = npairs(sample1, randoms, rbins, period=period)
            D1R = np.diff(D1R)
            if np.all(sample1 == sample2): #calculating the cross-correlation
                D2R = None
            else:
                D2R = npairs(sample2, randoms, rbins, period=period)
                D2R = np.diff(D2R)
            
            return D1R, D2R, RR
        #PBCs and randoms.
        elif randoms != None:
            if do_RR==True:
                RR = npairs(randoms, randoms, rbins, period=period)
                RR = np.diff(RR)
            else: RR=None
            if do_DR==True:
                D1R = npairs(sample1, randoms, rbins, period=period)
                D1R = np.diff(D1R)
            else: D1R=None
            if np.all(sample1 == sample2): #calculating the cross-correlation
                D2R = None
            else:
                if do_DR==True:
                    D2R = npairs(sample2, randoms, rbins, period=period)
                    D2R = np.diff(D2R)
                else: D2R=None
            
            return D1R, D2R, RR
        #PBCs and no randoms--calculate randoms analytically.
        elif randoms == None:
            #do volume calculations
            dv = nball_volume(rbins,k) #volume of spheres
            dv = np.diff(dv) #volume of shells
            global_volume = period.prod() #sexy
            
            #calculate randoms for sample1
            N1 = np.shape(sample1)[0]
            rho1 = N1/global_volume
            D1R = (N1)*(dv*rho1) #read note about pair counter
            
            #if not calculating cross-correlation, set RR exactly equal to D1R.
            if np.all(sample1 == sample2):
                D2R = None
                RR = D1R #in the analytic case, for the auto-correlation, DR==RR.
            else: #if there is a sample2, calculate randoms for it.
                N2 = np.shape(sample2)[0]
                rho2 = N2/global_volume
                D2R = N2*(dv*rho2) #read note about pair counter
                #calculate the random-random pairs.
                NR = N1*N2
                rhor = NR/global_volume
                RR = (dv*rhor) #RR is only the RR for the cross-correlation.

            return D1R, D2R, RR
        else:
            raise ValueError('Un-supported combination of PBCs and randoms provided.')
    
    def pair_counts(sample1, sample2, rbins, period, N_thread, do_auto, do_cross, do_DD, comm):
        """
        Count data pairs.
        """
        D1D1 = npairs(sample1, sample1, rbins, period=period)
        D1D1 = np.diff(D1D1)
        if np.all(sample1 == sample2):
            D1D2 = D1D1
            D2D2 = D1D1
        else:
            D1D2 = npairs(sample1, sample2, rbins, period=period)
            D1D2 = np.diff(D1D2)
            D2D2 = npairs(sample2, sample2, rbins, period=period)
            D2D2 = np.diff(D2D2)

        return D1D1, D1D2, D2D2
        
    def TP_estimator(DD,DR,RR,ND1,ND2,NR1,NR2,estimator):
        """
        two point correlation function estimator
        """
        if estimator == 'Natural':
            factor = ND1*ND2/(NR1*NR2)
            #DD/RR-1
            xi = (1.0/factor)*DD/RR - 1.0
        elif estimator == 'Davis-Peebles':
            factor = ND1*ND2/(ND1*NR2)
            #DD/DR-1
            xi = (1.0/factor)*DD/DR - 1.0
        elif estimator == 'Hewett':
            factor1 = ND1*ND2/(NR1*NR2)
            factor2 = ND1*NR2/(NR1*NR2)
            #(DD-DR)/RR
            xi = (1.0/factor1)*DD/RR - (1.0/factor2)*DR/RR
        elif estimator == 'Hamilton':
            #DDRR/DRDR-1
            xi = (DD*RR)/(DR*DR) - 1.0
        elif estimator == 'Landy-Szalay':
            factor1 = ND1*ND2/(NR1*NR2)
            factor2 = ND1*NR2/(NR1*NR2)
            #(DD - 2.0*DR + RR)/RR
            xi = (1.0/factor1)*DD/RR - (1.0/factor2)*2.0*DR/RR + 1.0
        else: 
            raise ValueError("unsupported estimator!")
        return xi
    
    def TP_estimator_requirements(estimator):
        """
        return booleans indicating which pairs need to be counted for the chosen estimator
        """
        if estimator == 'Natural':
            do_DD = True
            do_DR = False
            do_RR = True
        elif estimator == 'Davis-Peebles':
            do_DD = True
            do_DR = True
            do_RR = False
        elif estimator == 'Hewett':
            do_DD = True
            do_DR = True
            do_RR = True
        elif estimator == 'Hamilton':
            do_DD = True
            do_DR = True
            do_RR = True
        elif estimator == 'Landy-Szalay':
            do_DD = True
            do_DR = True
            do_RR = True
        else: 
            raise ValueError("unsupported estimator!")
        return do_DD, do_DR, do_RR
    
    do_DD, do_DR, do_RR = TP_estimator_requirements(estimator)
              
    if randoms is not None:
        N1 = len(sample1)
        N2 = len(sample2)
        NR = len(randoms)
    else: 
        N1 = 1.0
        N2 = 1.0
        NR = 1.0
    
    #count pairs
    D1D1,D1D2,D2D2 = pair_counts(sample1, sample2, rbins, period,\
                                 N_threads, do_auto, do_cross, do_DD, comm)
    D1R, D2R, RR = random_counts(sample1, sample2, randoms, rbins, period,\
                                 PBCs, k, N_threads, do_RR, do_DR, comm)
    
    if np.all(sample2==sample1):
        xi_11 = TP_estimator(D1D1,D1R,RR,N1,N1,NR,NR,estimator)
        return xi_11
    else:
        if (do_auto==True) & (do_cross==True): 
            xi_11 = TP_estimator(D1D1,D1R,RR,N1,N1,NR,NR,estimator)
            xi_12 = TP_estimator(D1D2,D1R,RR,N1,N2,NR,NR,estimator)
            xi_22 = TP_estimator(D2D2,D2R,RR,N2,N2,NR,NR,estimator)
            return xi_11, xi_12, xi_22
        elif (do_cross==True):
            xi_12 = TP_estimator(D1D2,D1R,RR,N1,N2,NR,NR,estimator)
            return xi_12
        elif (do_auto==True):
            xi_11 = TP_estimator(D1D1,D1R,D1R,N1,N1,NR,NR,estimator)
            xi_22 = TP_estimator(D2D2,D2R,D2R,N2,N2,NR,NR,estimator)
            return xi_11

