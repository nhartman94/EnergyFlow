"""Implementation of multigraph Generator class."""
from __future__ import absolute_import, division, print_function
import copy
import itertools
import numpy as np

# already checked that we have igraph
import igraph

from energyflow.algorithms import *

__all__ = ['Generator']

class Generator(VariableElimination):

    """
    A class that facilitates multigraph generation.

    Multiraphs are generated by first finding all simple graphs up to some nmax
    number of vertices, emax number of edges, and cmax VE complexity (which 
    depends on the particular VE implementation chosen). Next, weights are added
    to each of these simple graphs using integer partitions up to dome dmax number 
    of multigraph edges. Disconnected graphs are determined using the connected 
    graphs by using integer partitions to determine all unique multiplications of
    the connected graphs up to nmax and dmax.

    Since computation will ultimately be done with numpy.einsum, einstrings and
    einpaths are also computed for each simple graph

    Multigraphs and associated properties can be easily saved in a .npz file.
    """

    def __init__(self, dmax, nmax=None, emax=None, cmax=None, verbose=True,
                       ve_alg='numpy', np_optimize='greedy'):
        """
        Initializes a Generator object for generating multigraphs.

        Parameters
        ----------
        warn : bool, optional
            Controls whether or not a warning is printed if igraph cannot be imported.
        file : string, optional
            Filename to use in the optional warning.

        Returns
        -------
        output : {igraph, False}
            The igraph module if it was successfully imported, otherwise False.
        """

        # initialize base class
        VariableElimination.__init__(self, ve_alg, np_optimize)

        # store parameters
        self.dmax = dmax
        self.nmax = nmax if nmax is not None else self.dmax+1
        self.emax = emax if emax is not None else self.dmax
        self.cmax = cmax if cmax is not None else self.nmax
        self.verbose = verbose

        # setup N and e values to be used
        self.ns = list(range(2, self.nmax+1))
        self.emaxs = {n: min(self.emax, int(n/2*(n-1))) for n in self.ns}
        self.esbyn = {n: list(range(n-1, self.emaxs[n]+1)) for n in self.ns}
        self.dmaxs = {(n,e): self.dmax for n in self.ns for e in self.esbyn[n]}

        # setup storage containers
        containers = [{(n,e): [] for n in self.ns for e in self.esbyn[n]} for i in range(6)]
        (self.simple_graphs_d, self.edges_d, self.chis_d, 
         self.einpaths_d, self.einstrs_d, self.weights_d) = containers

        # get simple graphs
        self._generate_simple()

        # get weighted graphs
        self._generate_weights()

        # get disconnected graphs
        self._generate_disconnected()

    # generates simple graphs subject to constraints
    def _generate_simple(self):

        self.base_edges = {n: list(itertools.combinations(range(n), 2)) for n in self.ns}

        self._add_if_new(igraph.Graph.Full(2, directed=False), (2,1))

        # iterate over all combinations of n>2 and d
        for n in self.ns[1:]:
            for e in self.esbyn[n]:

                # consider adding new vertex
                if e-1 in self.esbyn[n-1]:

                    # iterate over all graphs with n-1, e-1
                    for seed_graph in self.simple_graphs_d[(n-1,e-1)]:

                        # iterate over vertices to attach to
                        for v in range(n-1):
                            new_graph = seed_graph.copy()
                            new_graph.add_vertices(1)
                            new_graph.add_edges([(v,n-1)])
                            self._add_if_new(new_graph, (n,e))

                # consider adding new edge to existing set of vertices
                if e-1 in self.esbyn[n]:

                    # iterate over all graphs with n, d-1
                    for seed_graph, seed_edges in zip(self.simple_graphs_d[(n,e-1)], 
                                                      self.edges_d[(n,e-1)]):

                        # iterate over edges that don't exist in graph
                        for new_edge in self._edge_filter(n, seed_edges):
                            new_graph = seed_graph.copy()
                            new_graph.add_edges([new_edge])
                            self._add_if_new(new_graph, (n,e))

        if self.verbose: 
            print('# of simple graphs by n:', self._count_simple_by_n())
            print('# of simple graphs by e:', self._count_simple_by_e())

    # adds simple graph if it is non-isomorphic to existing graphs and has a valid metric
    def _add_if_new(self, new_graph, ne):
        # check for isomorphism with existing graphs
        for graph in self.simple_graphs_d[ne]:
            if new_graph.isomorphic(graph): return

        # check that ve complexity for this graph is valid
        new_edges = new_graph.get_edgelist()
        self.ve(new_edges, ne[0])
        if self.chi > self.cmax: return
        
        # append graph and ve complexity to containers
        self.simple_graphs_d[ne].append(new_graph)
        self.edges_d[ne].append(new_edges)
        self.chis_d[ne].append(self.chi)

        einstr, einpath = self.einspecs()
        self.einstrs_d[ne].append(einstr)
        self.einpaths_d[ne].append(einpath)

    # generator for edges not already in list
    def _edge_filter(self, n, edges):
        for edge in self.base_edges[n]:
            if edge not in edges:
                yield edge

    # generates non-isomorphic graph weights subject to constraints
    def _generate_weights(self):

        # take care of the n=2 case:
        self.weights_d[(2,1)].append([(d,) for d in range(1, self.dmaxs[(2,1)]+1)])

        # get ordered integer partitions of d of length e for relevant values
        parts = {}
        for n in self.ns[1:]:
            for e in self.esbyn[n]:
                for d in range(e, self.dmaxs[(n,e)]+1):
                    if (d,e) not in parts:
                        parts[(d,e)] = list(int_partition_ordered(d, e))

        # iterate over the rest of ns
        for n in self.ns[1:]:

            # iterate over es for which there are simple graphs
            for e in self.esbyn[n]:

                # iterate over simple graphs
                for graph in self.simple_graphs_d[(n,e)]:
                    weightings = []

                    # iterate over valid d for this graph
                    for d in range(e, self.dmaxs[(n,e)]+1):

                        # iterate over int partitions
                        for part in parts[(d,e)]:

                            # check if isomorphic to existing
                            iso = False
                            for weighting in weightings:
                                if graph.isomorphic_vf2(other=graph, 
                                                        edge_color1=weighting, 
                                                        edge_color2=part): 
                                    iso = True
                                    break
                            if not iso: weightings.append(part)
                    self.weights_d[(n,e)].append(weightings)

        if self.verbose: 
            print('# of weightings by n:', self._count_weighted_by_n())
            print('# of weightings by d:', self._count_weighted_by_d())

    def _generate_disconnected(self):

        """ 
        Column descriptions:
        n - number of vertices in graph
        e - number of edges in (underlying) simple graph
        d - number of edges in multigraph
        k - unique index for graphs with a fixed (n,d)
        g - index of simple edges in edges
        w - index of weights in weights
        c - complexity, with respect to some VE algorithm
        p - number of prime factors for this EFP
        """

        self.cols = ['n','e','d','k','g','w','c','p']
        self.__dict__.update({col+'_ind': i for i,col in enumerate(self.cols)})
        self.connected_specs = []
        self.edges, self.weights, self.einstrs, self.einpaths = [], [], [], []
        self.ks, self.ndk2i = {}, {}
        g = w = i = 0
        for ne in sorted(self.chis_d.keys()):
            n, e = ne
            z = zip(self.edges_d[ne], self.weights_d[ne], self.chis_d[ne], 
                    self.einstrs_d[ne], self.einpaths_d[ne])
            for edges, weights, chi, es, ep in z:
                for weighting in weights:
                    d = sum(weighting)
                    k = self.ks.setdefault((n,d), 0)
                    self.ks[(n,d)] += 1
                    self.connected_specs.append([n, e, d, k, g, w, chi, 1])
                    self.ndk2i[(n,d,k)] = i
                    self.weights.append(weighting)
                    w += 1
                    i += 1
                self.edges.append(edges)
                self.einstrs.append(es)
                self.einpaths.append(ep)
                g += 1

        self.connected_specs = np.asarray(self.connected_specs)
        
        disc_formulae, disc_specs = [], []

        # disconnected start at N>=4
        for n in range(4,2*self.dmax+1):

            # partitions with no 1s, no numbers > self.nmax, and not the trivial partition
            good_part = lambda x: (1 not in x and max(x) <= self.nmax and len(x) > 1)
            n_parts = [tuple(x) for x in int_partition_unordered(n) if good_part(x)]
            n_parts.sort(key=len)

            # iterate over all ds
            for d in range(int(n/2),self.dmax+1):

                # iterate over all n_parts
                for n_part in n_parts:
                    n_part_len = len(n_part)

                    # get w_parts of the right length
                    d_parts = [x for x in int_partition_unordered(d) if len(x) == n_part_len]

                    # ensure that we found some
                    if len(d_parts) == 0: continue

                    # usage of set and sorting is important to avoid duplicates
                    specs = set()

                    # iterate over all orderings of the n_part
                    for n_part_ord in set([x for x in itertools.permutations(n_part)]):

                        # iterate over all w_parts
                        for d_part in d_parts:

                            # construct spec. sorting ensures we don't get duplicates in specs
                            spec = tuple(sorted([(npo,dp) for npo,dp in zip(n_part_ord,d_part)]))

                            # check that we have the proper primes to calculate this spec
                            good = True
                            for pair in spec:

                                # w needs to be in range n-1,wmaxs[n]
                                if pair not in self.ks:
                                    good = False
                                    break
                            if good:
                                specs.add(spec)

                    # iterate over all specs that we found
                    for spec in specs:

                        # keep track of how many we added
                        kcount = 0 if (n,d) not in self.ks else self.ks[(n,d)]

                        # iterate over all possible formula implementations with the different ndk
                        for kspec in itertools.product(*[range(self.ks[factor]) \
                                                         for factor in spec]):

                            # iterate over factors
                            formula = []
                            cmax = emax = 0 
                            for (nn,dd),kk in zip(spec,kspec):

                                # select original simple graph
                                ind = self.ndk2i[(nn,dd,kk)]

                                # add col index of factor to formula
                                formula.append(ind)
                                cmax = max(cmax, self.connected_specs[ind,self.c_ind])
                                emax = max(emax, self.connected_specs[ind,self.e_ind])

                            # append to stored array
                            disc_formulae.append(tuple(sorted(formula)))
                            disc_specs.append([n, emax, d, kcount, -1, -1, cmax, len(kspec)])
                            kcount += 1

        # ensure unique formulae (deals with possible degeneracy in selection of factors)
        disc_form_set = set()
        mask = np.asarray([not(form in disc_form_set or disc_form_set.add(form)) \
                           for form in disc_formulae])

        # store as numpy arrays
        self.disc_formulae = np.asarray(disc_formulae)[mask]
        self.disc_specs = np.asarray(disc_specs)[mask]

        if len(self.disc_specs.shape) == 2:
            self.specs = np.concatenate((self.connected_specs, self.disc_specs))
        else:
            self.specs = self.connected_specs

    def _count_simple_by_n(self):
        return {n: np.sum([len(self.edges_d[(n,e)]) for e in self.esbyn[n]]) for n in self.ns}

    def _count_simple_by_e(self):
        return {e: np.sum([len(self.edges_d[(n,e)]) for n in self.ns if (n,e) in self.edges_d]) \
                           for e in range(1,self.emax+1)}

    def _count_weighted_by_n(self):
        return {n: np.sum([len(weights) for e in self.esbyn[n] \
                           for weights in self.weights_d[(n,e)]]) for n in self.ns}

    def _count_weighted_by_d(self):
        counts = {d: 0 for d in range(1,self.dmax+1)}
        for n in self.ns:
            for e in self.esbyn[n]:
                for weights in self.weights_d[(n,e)]:
                    for weighting in weights: counts[sum(weighting)] += 1
        return counts

    def save(self, filename):
        np.savez(filename, **{'ve_alg':        self.ve.ve_alg,
                              'cols':          self.cols,
                              'specs':         self.specs,
                              'disc_formulae': self.disc_formulae,
                              'edges':         self.edges,
                              'einstrs':       self.einstrs,
                              'einpaths':      self.einpaths,
                              'weights':       self.weights})