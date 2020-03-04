from copy import copy
from itertools import zip_longest
from concurrent import futures as cf

import numpy as np
from scipy.stats import gamma, nbinom
from numba import jit, njit

__all__ = ['abc', 'acceptance_fraction', 'simulate_outbreak',
           'quarantine_fraction']


@njit
def sample_nbinom(n, p, size):
    nb = np.zeros(size)
    for i in range(size):
        nb[i] = np.random.poisson(np.random.gamma(n, (1 - p) / p))
    return nb


def grouper(iterable, n, fillvalue=None):
    """
    Collect data into fixed-length chunks or blocks.

    grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"

    Source: https://docs.python.org/3/library/itertools.html#recipes
    """
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


def abc(function, n_processes, R0_grid, n_grid_points_per_process,
        **parameters):
    # https://stackoverflow.com/a/15143994
    executor = cf.ProcessPoolExecutor(max_workers=n_processes)
    futures = [executor.submit(function, group, **parameters)
               for group in grouper(R0_grid, n_grid_points_per_process)]
    cf.wait(futures)


def simulate_outbreak_slow(R0, k, n, D, gamma_shape, max_time,
                           days_elapsed_max, max_cases):
    """
    Simulate an outbreak.

    Parameters
    ----------
    R0 : float
    k : float
    n : float
    D : float
    gamma_shape : float
    max_time : float
    days_elapsed_max : float
    max_cases : float

    Returns
    -------
    times : `~numpy.ndarray`
        Times of incidence measurements
    cumulative_incidence : `~numpy.ndarray`
        Cumulative incidence (total cases) at each time
    """
    times = n * [0]
    cumulative_incidence = copy(n)
    t = np.array(times)
    cases = copy(n)
    incidence = [n]
    t_mins = [0]

    while (cases > 0) and (t.min() < days_elapsed_max) and (
            cumulative_incidence < max_cases):
        secondary = nbinom.rvs(n=k, p=k / (k + R0), size=cases)

        # Vectorized approach (optimized for speed in Python)
        inds = np.arange(0, secondary.max())
        gamma_size = (secondary.shape[0], secondary.max())
        t_new = np.ma.array(t[:, None] + gamma.rvs(D / gamma_shape,
                                                   size=gamma_size),
                            mask=secondary[:, None] <= inds)

        times_in_bounds = ((t_new.data < max_time) &
                           np.logical_not(t_new.mask))
        cases = np.count_nonzero(times_in_bounds)
        cumulative_incidence += cases
        t = t_new[times_in_bounds].copy()
        if cases > 0:
            t_mins.append(t.min())
            incidence.append(cases)

    incidence = np.array(incidence)
    epidemic_curve = incidence.cumsum()
    t_mins = np.array(t_mins)
    return t_mins, epidemic_curve


@njit
def simulate_outbreak(R0, k, n, D, gamma_shape, max_time,
                      days_elapsed_max, max_cases, seed=None, f_Q=0):
    """
    Simulate an outbreak.

    Parameters
    ----------
    R0 : float
        Reproductive number
    k : float
        Overdispersion factor
    n : float
        Number of index cases
    D : float
        Generation time interval
    gamma_shape : float
        Generation interval (Gamma distribution) shape parameter
    max_time : float
        Maximum simulation time
    days_elapsed_max : float
        Maximum time elapsed from index case
    max_cases : float
        Maximum number of cases simulated
    seed : int (optional)
        Random seed
    f_Q : float (optional)
        The fraction of cases that are perfectly quarantined. Default is zero.

    Returns
    -------
    times : `~numpy.ndarray`
        Times of incidence measurements
    cumulative_incidence : `~numpy.ndarray`
        Cumulative incidence (total cases) at each time
    """
    if seed is not None:
        np.random.seed(seed)
    cumulative_incidence = int(n)
    t = np.zeros(n)
    cases = int(n)
    incidence = [n]
    t_mins = [0]

    while (cases > 0) and (t.min() < days_elapsed_max) and (
            cumulative_incidence < max_cases):

        # Generate secondary cases from negative binomial distribution
        secondary = sample_nbinom(n=k, p=k/(k+R0), size=cases)


        # Randomly choose fraction of cases to be perfectly quarantined
        fraction_keep = int(len(secondary) * (1 - f_Q))
        init_secondary_shape = secondary.shape[0]
        secondary = np.random.choice(secondary, fraction_keep,
                                     replace=False)

        secondary = np.concatenate((secondary,
                                    np.zeros(int(init_secondary_shape -
                                                 fraction_keep))))

        inds = np.arange(0, secondary.max())
        gamma_size = (secondary.shape[0], int(secondary.max()))
        g = np.random.standard_gamma(D / gamma_shape, size=gamma_size)

        t_new = np.expand_dims(t, 1) + g
        mask = np.expand_dims(secondary, 1) <= inds
        times_in_bounds = ((t_new < max_time) &
                           np.logical_not(mask))
        cases = np.count_nonzero(times_in_bounds)
        cumulative_incidence += cases

        t = t_new.ravel()[times_in_bounds.ravel()].copy()
        if cases > 0:
            t_mins.append(t.min())
            incidence.append(cases)

    incidence = np.array(incidence)
    epidemic_curve = incidence.cumsum()
    t_mins = np.array(t_mins)
    return t_mins, epidemic_curve


def acceptance_fraction(R0_grid, k_grid, trials, D_min, D_max, n_min, n_max,
                        max_cases, gamma_shape_min, gamma_shape_max, max_time,
                        days_elapsed_min, days_elapsed_max, min_number_cases,
                        max_number_cases, samples_path):

    accepted_grid = []

    D_chain = []
    n_chain = []
    R0_chain = []
    k_chain = []
    days_elapsed_chain = []
    gamma_shape_chain = []

    R0_grid = np.array(R0_grid)

    for i, R0 in enumerate(R0_grid):
        accept_k = []
        for j, k in enumerate(k_grid):
            accepted = []
            for n in range(trials):
                D = D_min + (D_max - D_min) * np.random.rand()
                n = np.random.randint(n_min, n_max)
                gamma_shape = (gamma_shape_min + (gamma_shape_max -
                                                  gamma_shape_min) *
                               np.random.rand())
                days_elapsed = (max(days_elapsed_min) +
                                (max(days_elapsed_max) - max(days_elapsed_min)
                                 ) * np.random.rand())

                t_mins, cum_inc = simulate_outbreak(R0, k, n, D, gamma_shape,
                                                    max_time, days_elapsed,
                                                    max_cases)

                if t_mins.max() >= days_elapsed:
                    delta_t = (np.array(days_elapsed_min) -
                               max(days_elapsed_min))
                    cases_at_obs_times = 10**np.interp(days_elapsed +
                                                       delta_t, t_mins,
                                                       np.log10(cum_inc))

                    accept = ((np.asarray(min_number_cases) <
                               cases_at_obs_times) &
                              (cases_at_obs_times <
                               np.asarray(max_number_cases))).all()

                    accepted.append(accept)

                    if accept:
                        D_chain.append(D)
                        n_chain.append(n)
                        R0_chain.append(R0)
                        k_chain.append(k)
                        days_elapsed_chain.append(days_elapsed)
                        gamma_shape_chain.append(gamma_shape)

            if len(accepted) > 0:
                accepted_fraction = np.count_nonzero(accepted) / len(accepted)
            else:
                accepted_fraction = 0

            accept_k.append(accepted_fraction)

        accepted_grid.append(accept_k)

    samples = np.vstack([R0_chain, k_chain, D_chain, n_chain,
                         days_elapsed_chain, gamma_shape_chain]).T
    np.save(samples_path.format(R0_grid[0]), samples)


def quarantine_fraction(R0_grid, k_grid, trials, D_min, D_max, n_min, n_max,
                        max_cases, gamma_shape_min, gamma_shape_max, max_time,
                        days_elapsed_min, days_elapsed_max, min_number_cases,
                        max_number_cases, samples_path, f_Q):

    accepted_grid = []

    D_chain = []
    n_chain = []
    R0_chain = []
    k_chain = []
    days_elapsed_chain = []
    gamma_shape_chain = []

    R0_grid = np.array(R0_grid)

    for i, R0 in enumerate(R0_grid):
        accept_k = []
        for j, k in enumerate(k_grid):
            accepted = []
            for n in range(trials):
                D = D_min + (D_max - D_min) * np.random.rand()
                n = np.random.randint(n_min, n_max)
                gamma_shape = (gamma_shape_min + (gamma_shape_max -
                                                  gamma_shape_min) *
                               np.random.rand())
                days_elapsed = (max(days_elapsed_min) +
                                (max(days_elapsed_max) - max(days_elapsed_min)
                                 ) * np.random.rand())

                t_mins, cum_inc = simulate_outbreak(R0, k, n, D, gamma_shape,
                                                    max_time, days_elapsed,
                                                    max_cases, f_Q=f_Q)

                if t_mins.max() > max(days_elapsed_max):
                    # Outbreak is still ongoing
                    accept = False
                elif cum_inc.max() > max_cases:
                    # Outbreak has tons of cases
                    accept = False
                else:
                    # Outbreak has terminated:
                    accept = True

                if accept:
                    D_chain.append(D)
                    n_chain.append(n)
                    R0_chain.append(R0)
                    k_chain.append(k)
                    days_elapsed_chain.append(days_elapsed)
                    gamma_shape_chain.append(gamma_shape)

            if len(accepted) > 0:
                accepted_fraction = np.count_nonzero(accepted) / len(accepted)
            else:
                accepted_fraction = 0

            accept_k.append(accepted_fraction)

        accepted_grid.append(accept_k)

    samples = np.vstack([R0_chain, k_chain, D_chain, n_chain,
                         days_elapsed_chain, gamma_shape_chain]).T
    np.save(samples_path.format(R0_grid[0]), samples)
