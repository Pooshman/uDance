import copy
import glob
import json
import multiprocessing as mp
import sys
from os.path import join
from pathlib import Path
from random import Random

import treeswift as ts

from uDance.PoolPartitionWorker import PoolPartitionWorker
from uDance.count_occupancy import count_occupancy
from uDance.newick_extended import read_tree_newick
from uDance.prep_partition_alignments import prep_partition_alignments
from uDance.treecluster_sum import min_tree_coloring_sum_max


# inputs: a placement tree
# max number of things in each cluster


def aggregate_placements(index_to_node_map, placements):
    for placement in placements:
        for i, seqname in enumerate(placement['n']):
            index = placement['p'][i][0]
            index_to_node_map[index].placements += [seqname]


def closest_merge(x, y):
    return x[0] + y, x[1]


def set_closest_three_directions(tree, occupancy_threshold):
    for node in tree.traverse_postorder():
        node.repr_tree = {'down': ts.Tree(is_rooted=False)}
        node.repr_tree['down'].root.edge_length = node.edge_length
        if node.is_leaf():
            # node.closest[0] is left. node.closest[1] is right. node.closest[2] is high occupancy
            node.repr_tuple = {'down': [(0, node), (0, node), (0, node)]}
            node.repr_tree['down'].root.label = node.label
            node.repr_tree['down'].root.outgroup = True
        else:
            closests = [
                min(map(lambda x: closest_merge(x, chd.edge_length), chd.repr_tuple['down'][:2]))
                for chd in node.children
            ]
            occups = sorted([closest_merge(chd.repr_tuple['down'][2], chd.edge_length) for chd in node.children])
            if occups[0][1].occupancy <= occupancy_threshold and occups[1][1].occupancy > occups[0][1].occupancy:
                theoccup = [occups[1]]
            else:
                theoccup = [occups[0]]
            node.repr_tuple = {'down': closests + theoccup}
            for chd in node.children:
                chdcopy = chd.repr_tree['down'].__copy__()
                node.repr_tree['down'].root.add_child(chdcopy.root)
            labs = list(set([x[1].label for x in node.repr_tuple['down']]))
            node.repr_tree['down'] = node.repr_tree['down'].extract_tree_with(labels=labs, suppress_unifurcations=True)
            for e in node.repr_tree['down'].traverse_postorder(internal=False):
                e.outgroup = True

    for node in tree.traverse_preorder():
        if node == tree.root:
            node.repr_tuple['up'] = [(float('inf'), None), (float('inf'), None), (float('inf'), None)]
            node.repr_tree['up'] = None
        else:
            node.repr_tree['up'] = ts.Tree(is_rooted=False)
            node.repr_tree['up'].root.edge_length = node.edge_length
            sib = [chd for chd in node.parent.children if chd != node][0]  # assumes binary tree
            closests = [min(map(lambda x: closest_merge(x, node.edge_length), node.parent.repr_tuple['up'][:2]))]
            closests += [
                min(map(lambda x: closest_merge(x, node.edge_length + sib.edge_length), sib.repr_tuple['down'][:2]))
            ]
            occups = [closest_merge(node.parent.repr_tuple['up'][2], node.edge_length)]
            occups += [closest_merge(sib.repr_tuple['down'][2], node.edge_length + sib.edge_length)]
            occups = sorted(occups)

            if occups[1][1] is None:
                theoccup = [occups[0]]
            elif occups[0][1].occupancy < occupancy_threshold and occups[1][1].occupancy > occups[0][1].occupancy:
                theoccup = [occups[1]]
            else:
                theoccup = [occups[0]]
            node.repr_tuple['up'] = closests + theoccup

            drecs = list(zip([node.parent, sib], ['up', 'down']))
            for nei, drec in drecs:
                if nei.repr_tree[drec] is not None:
                    chdcopy = nei.repr_tree[drec].__copy__()
                    node.repr_tree['up'].root.add_child(chdcopy.root)
            valids = [pr for pr in node.repr_tuple['up'] if pr[1] is not None]
            labs = list(set([x[1].label for x in valids]))
            node.repr_tree['up'] = node.repr_tree['up'].extract_tree_with(labels=labs, suppress_unifurcations=True)
            for e in node.repr_tree['up'].traverse_postorder(internal=False):
                e.outgroup = True


def build_color_spanning_tree(tstree):
    color_spanning_tree = ts.Tree()
    r = color_spanning_tree.root
    r.color = tstree.root.color
    r.label = str(r.color)

    color_to_node_map = {r.color: r}
    for n in tstree.traverse_preorder():
        if n.is_leaf():
            continue
        for c in n.children:
            if c.color != n.color and c.color not in color_to_node_map:
                par = color_to_node_map[n.color]
                child = ts.Node()
                par.add_child(child)
                color_to_node_map[c.color] = child
                child.color = c.color
                child.label = str(child.color)

    return color_spanning_tree, color_to_node_map


def balance_jobs(lst, num_jobs):
    def chunks(l, n):
        """Yield n number of striped chunks from l."""
        for i in range(0, n):
            yield l[i::n]

    r = Random(42)
    # per = max(1, len(lst)//num_jobs)
    r.shuffle(lst)
    group = chunks(lst, num_jobs)

    return [list(map(lambda x: x[1], sorted(g, reverse=True))) for g in group]


def decompose(options):
    if options.num_tasks < 1:
        sys.stderr.write('Invalid number of tasks. Number of tasks is set to the minimum value: 1.\n')
        options.num_tasks = 1

    with open(options.jplace_fp) as f:
        jp = json.load(f)
    tstree = read_tree_newick(jp['tree'])

    index_to_node_map = {}
    for e in tstree.traverse_postorder():
        e.placements = []
        if e != tstree.root:
            index_to_node_map[e.edge_index] = e
    aggregate_placements(index_to_node_map, jp['placements'])

    # min_tree_coloring_sum(tstree, float(options.threshold))
    min_tree_coloring_sum_max(tstree, float(options.threshold), options.edge_threshold)
    occupancy, num_genes = count_occupancy(options.alignment_dir_fp, options.protein_seqs)

    for e in tstree.traverse_postorder(internal=False):
        if e.label in occupancy:
            e.occupancy = occupancy[e.label]
        else:
            e.occupancy = 0

    set_closest_three_directions(tstree, num_genes * options.occupancy_threshold)

    # colors = {}
    # for n in tstree.traverse_postorder():
    #     if n.color not in colors:
    #         colors[n.color] = [n]
    #     else:
    #         colors[n.color] += [n]

    Path(options.output_fp).mkdir(parents=True, exist_ok=True)
    color_spanning_tree, color_to_node_map = build_color_spanning_tree(tstree)
    color_spanning_tree.write_tree_newick(join(options.output_fp, 'color_spanning_tree.nwk'))
    copyts = tstree.extract_tree_with(labels=tstree.labels())
    for n in copyts.traverse_preorder():
        n.outgroup = False

    traversal = list(zip(tstree.traverse_preorder(), copyts.traverse_preorder()))
    tree_catalog = {}

    outgroup_map = {-1: {'up': None, 'ownsup': False, 'children': dict()}}
    for n, ncopy in traversal:
        ncopy.resolved_randomly = n.resolved_randomly
        ncopy.placements = n.placements
        if not n.is_root() and hasattr(n, 'edge_index'):
            ncopy.edge_index = n.edge_index
        if n.is_leaf():
            continue
        cl, cr = n.children
        clcopy, crcopy = ncopy.children

        #                   C2
        # case 1     C1  <
        #                   C3
        if cl.color != n.color and cr.color != n.color and cl.color != cr.color:
            ncopy.remove_child(clcopy)
            outcl = copy.deepcopy(cl.repr_tree['down'])
            outgroup_map[n.color]['children'][cl.color] = outcl.newick()
            ncopy.add_child(outcl.root)

            ncopy.remove_child(crcopy)
            outcr = copy.deepcopy(cr.repr_tree['down'])
            outgroup_map[n.color]['children'][cr.color] = outcr.newick()
            ncopy.add_child(outcr.root)

            newTreeL = ts.Tree()
            newTreeL.is_rooted = False
            newTreeL.root.outgroup = False
            outcr = copy.deepcopy(cr.repr_tree['down'])
            newTreeL.root.add_child(outcr.root)
            outup = copy.deepcopy(n.repr_tree['up'])
            if outup:
                newTreeL.root.add_child(outup.root)
            outgroup_map[cl.color] = {'up': newTreeL.newick(), 'ownsup': True, 'children': dict()}
            newTreeL.root.add_child(clcopy)
            tree_catalog[cl.color] = newTreeL

            newTreeR = ts.Tree()
            newTreeR.is_rooted = False
            newTreeR.root.outgroup = False
            outcl = copy.deepcopy(cl.repr_tree['down'])
            newTreeR.root.add_child(outcl.root)
            outup = copy.deepcopy(n.repr_tree['up'])
            if outup:
                newTreeR.root.add_child(outup.root)
            outgroup_map[cr.color] = {'up': newTreeR.newick(), 'ownsup': True, 'children': dict()}
            newTreeR.root.add_child(crcopy)
            tree_catalog[cr.color] = newTreeR

        #                   C1
        # case 2     C1  <
        #                   C3
        if cl.color != n.color and cr.color == n.color and cl.color != cr.color:
            ncopy.remove_child(clcopy)
            outcl = copy.deepcopy(cl.repr_tree['down'])
            outgroup_map[n.color]['children'][cl.color] = outcl.newick()
            ncopy.add_child(outcl.root)

            newTreeL = ts.Tree()
            newTreeL.is_rooted = False
            newTreeL.root.outgroup = False
            outcr = copy.deepcopy(cr.repr_tree['down'])
            newTreeL.root.add_child(outcr.root)
            outup = copy.deepcopy(n.repr_tree['up'])
            if outup:
                newTreeL.root.add_child(outup.root)
            outgroup_map[cl.color] = {'up': newTreeL.newick(), 'ownsup': True, 'children': dict()}
            newTreeL.root.add_child(clcopy)
            tree_catalog[cl.color] = newTreeL

        #                   C3
        # case 3     C1  <
        #                   C1
        if cl.color == n.color and cr.color != n.color and cl.color != cr.color:
            ncopy.remove_child(crcopy)
            outcr = copy.deepcopy(cr.repr_tree['down'])
            outgroup_map[n.color]['children'][cr.color] = outcr.newick()
            ncopy.add_child(outcr.root)

            newTreeR = ts.Tree()
            newTreeR.is_rooted = False
            newTreeR.root.outgroup = False
            outcl = copy.deepcopy(cl.repr_tree['down'])
            newTreeR.root.add_child(outcl.root)
            outup = copy.deepcopy(n.repr_tree['up'])
            if outup:
                newTreeR.root.add_child(outup.root)
            outgroup_map[cr.color] = {'up': newTreeR.newick(), 'ownsup': True, 'children': dict()}
            newTreeR.root.add_child(crcopy)
            tree_catalog[cr.color] = newTreeR

        #                   C3
        # case 4     C1  <
        #                   C3
        if cl.color != n.color and cr.color != n.color and cl.color == cr.color:
            ncopy.remove_child(clcopy)
            outcl = copy.deepcopy(cl.repr_tree['down'])
            ncopy.add_child(outcl.root)

            ncopy.remove_child(crcopy)
            outcr = copy.deepcopy(cr.repr_tree['down'])
            ncopy.add_child(outcr.root)

            # special case for outgroup map
            # create a throwaway tree to print its newick
            newTreeR = ts.Tree()
            newTreeR.is_rooted = False
            newTreeR.root.outgroup = False
            outcl = copy.deepcopy(cl.repr_tree['down'])
            newTreeR.root.add_child(outcl.root)
            outcr = copy.deepcopy(cr.repr_tree['down'])
            newTreeR.root.add_child(outcr.root)
            outgroup_map[n.color]['children'][cr.color] = newTreeR.newick()

            newTree = ts.Tree()
            newTree.is_rooted = False
            newTree.root.outgroup = False
            outup = copy.deepcopy(n.repr_tree['up'])
            if outup:
                newTree.root.add_child(outup.root)
                outgroup_map[cr.color] = {'up': newTree.newick(), 'ownsup': False, 'children': dict()}
            else:
                outgroup_map[cr.color] = {'up': None, 'ownsup': False, 'children': dict()}
            newTree.root.add_child(clcopy)
            newTree.root.add_child(crcopy)
            tree_catalog[cl.color] = newTree

    with open(join(options.output_fp, 'outgroup_map.json'), 'w') as f:
        f.write(json.dumps(outgroup_map, sort_keys=True, indent=4))
    all_outgroups = []
    for n, dc in outgroup_map.items():
        if dc['up']:
            all_outgroups += [leaf.label for leaf in ts.read_tree_newick(dc['up']).traverse_postorder(internal=False)]
        if dc['children']:
            for nc, dcc in dc['children'].items():
                all_outgroups += [leaf.label for leaf in ts.read_tree_newick(dcc).traverse_postorder(internal=False)]
    all_outgroups = list(set(all_outgroups))
    with open(join(options.output_fp, 'all_outgroups.txt'), 'w') as f:
        f.write('\n'.join(all_outgroups) + '\n')
    for i, t in tree_catalog.items():
        for e in t.traverse_postorder():
            if not (hasattr(e, 'outgroup') and e.outgroup is True):
                e.outgroup = False
            if not (hasattr(e, 'resolved_randomly') and e.resolved_randomly is True):
                e.resolved_randomly = False
    # stitching algorithm:
    # preorder traversal color_to_node_map
    # for each node n, find the joint j in tstree.
    # find LCA of j.left_closest_child and j.right_closest_child in n
    # replace it with n.children

    partition_worker = PoolPartitionWorker()
    partition_worker.set_class_attributes(options)

    pool = mp.Pool(options.num_thread)
    species_path_list = pool.starmap(partition_worker.worker, tree_catalog.items())
    pool.close()
    pool.join()

    all_scripts = prep_partition_alignments(
        options.alignment_dir_fp,
        options.protein_seqs,
        [pth for pth, skip in species_path_list if not skip],
        options.num_thread,
        options.subalignment_length,
        options.fragment_length,
    )

    tasks = balance_jobs(all_scripts, options.num_tasks)
    for i, t in enumerate(tasks):
        main_script = open(join(options.output_fp, 'main_raxml_script_%s.sh' % str(i)), 'w')
        main_script.write('\n'.join(t))
        main_script.write('\n')
        main_script.close()

    indvalns = glob.glob(join(options.output_fp, '*/*/aln.fa'))

    with open(join(options.output_fp, 'jobsizes.txt'), 'w', buffering=10000000) as js:
        for p in indvalns:
            count = 0
            for line in open(p).readlines():
                if line.startswith('>'):
                    count += 1
            par, gene = p.split('/')[-3:-1]
            js.write(par + '\t' + gene + '\t' + str(count) + '\n')

    # TODO a bipartition for each alignment
    for i, j in tree_catalog.items():
        count = 0
        for n in j.traverse_postorder():
            try:
                if n.is_leaf():
                    count += 1
                if n.outgroup is False and hasattr(n, 'placements'):
                    count += len(n.placements)
            except e:
                pass
        print(i, count)
