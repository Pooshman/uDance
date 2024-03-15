from os.path import join, exists
from glob import glob
from pathlib import Path
from sys import stderr, exit, stdout
import shutil
from subprocess import Popen, PIPE
import treeswift as ts
from statistics import median
from kmeans1d import cluster

from uDance.expand_dedupe_newick import expand_dedupe_newick


class PoolAstralWorker:
    options = None
    astral_libdir = None
    astral_mp_exec = None

    @classmethod
    def set_class_attributes(cls, options, astral_mp_exec, astral_libdir):
        cls.options = options
        cls.astral_mp_exec = astral_mp_exec
        cls.astral_libdir = astral_libdir

    @classmethod
    def worker(cls, partition_output_dir):
        if exists(Path(join(partition_output_dir, 'skip_partition'))):
            jlabs = list(ts.read_tree_newick(join(partition_output_dir, 'astral_constraint.nwk')).labels())
            backbone_tree_fp = join(partition_output_dir, '../../backbone.nwk')
            backbone_tree = ts.read_tree_newick(backbone_tree_fp)
            extracted_tree = backbone_tree.extract_tree_with(jlabs)
            input_tree_path = join(partition_output_dir, 'astral_input.trees')
            extracted_tree.write_tree_newick(input_tree_path)
            for i in ['incremental', 'updates']:
                newick_path = join(partition_output_dir, 'astral_output.%s.nwk' % i)
                extracted_tree.write_tree_newick(newick_path)
                #     newickbl_path = join(partition_output_dir, "astral_output.%s.nwk.bl" % i)
                #     extracted_tree.write_tree_newick(newickbl_path)
                log_path = join(partition_output_dir, 'astral.%s.log' % i)
                with open(log_path, 'w') as f:
                    f.write('Final quartet score is 1\n')
            return
        genes = glob(join(partition_output_dir, '*', ''))
        median_map = dict()
        genetrees = dict()
        for gene in genes:
            # if cls.options.method == 'iqtree':
            #     best = Path(join(gene, 'RUN.treefile'))
            # elif cls.options.method == 'raxml-ng':
            #     best = Path(join(gene, 'RUN.raxml.bestTree'))
            # elif cls.options.method == 'raxml-8':
            best = Path(join(gene, 'bestTree.nwk'))
            # bestCollapsed = Path(join(gene, 'RUN.raxml.bestTreeCollapsed'))
            # if bestCollapsed.is_file():
            #     raxtree = bestCollapsed
            if best.is_file():
                raxtree = best
            else:
                stderr.write('%s/bestTree.nwk does not exist. RAxML job is corrupted. \n' % gene)
                continue
            with open(raxtree) as f:
                treestr = f.readline()
            tf = ts.read_tree_newick(treestr)
            lpps = [float(i.label.replace('/', '')) for i in tf.traverse_postorder(leaves=False) if i.label]
            if len(lpps) > 0:
                median_map[gene] = median(lpps)
            # contract after computing the median
            tf.contract_low_support(threshold=cls.options.contract_threshold)
            treestr = str(tf) + '\n'
            dupmap_file = Path(join(gene, 'dupmap.txt'))
            if dupmap_file.is_file():
                dmp = list(map(lambda x: x.strip().split('\t'), open(dupmap_file).readlines()))
                genetrees[gene] = expand_dedupe_newick(treestr, dmp)
            else:
                genetrees[gene] = treestr

        # remove outlier genes. outlier is defined as having lower median local posterior probability than majority
        # we use 1d k-means (k=2) for outlier detection.
        clusters, centroids = cluster(list(median_map.values()), k=2)
        if (1 - cls.options.outlier_sizelimit) < sum(clusters) / len(clusters) < 1 and centroids[1] - centroids[
            0
        ] > cls.options.centroid_difference:
            min_median = min([v for i, v in enumerate(median_map.values()) if clusters[i] == 1])
            numdiscard = len(clusters) - sum(clusters)
            print(
                'In cluster %s, %d gene tree(s) with lower than '
                '%.2f median lpp are discarded.' % (partition_output_dir, numdiscard, min_median),
                file=stderr,
            )
            confident_trees = {
                gene: ts.read_tree_newick(genetrees[gene])
                for i, gene in enumerate(median_map.keys())
                if clusters[i] == 1
            }
        else:
            confident_trees = {gene: ts.read_tree_newick(tstr) for gene, tstr in genetrees.items()}
        # remove low occupancy sequences from all gene trees
        occups = dict()
        for gn, t in confident_trees.items():
            labels = [i.label for i in t.traverse_postorder(internal=False)]
            for l in labels:
                if l not in occups:
                    occups[l] = 1
                else:
                    occups[l] += 1
        low_occups = set([tag for tag in occups if occups[tag] < cls.options.occupancy_threshold])
        try:
            anchor_seqs = set(ts.read_tree_newick(join(partition_output_dir, 'astral_constraint.nwk')).labels())
        except:
            anchor_seqs = set()
        low_occups = list(low_occups.difference(anchor_seqs))
        if len(low_occups) > 0:
            print('In cluster %s, following low occupancy sequences are removed.' % partition_output_dir, file=stderr)
            print(low_occups, file=stderr)

        tobepopped = []
        for gene in confident_trees.keys():
            nolowtree = confident_trees[gene].extract_tree_without(low_occups)
            nolowtree.suppress_unifurcations()
            nolowtree.is_rooted = False
            confident_trees[gene] = nolowtree
            if len(list(confident_trees[gene].labels())) < 4:
                tobepopped.append(gene)
        for g in tobepopped:
            confident_trees.pop(g)

        expanded_trees = []
        for gene in confident_trees.keys():
            extreepath = join(gene, 'raxml.expanded.nwk')
            expanded_trees.append(extreepath)
            with open(extreepath, 'w') as out:
                out.write(str(confident_trees[gene]) + '\n')
        astral_input_file = join(partition_output_dir, 'astral_input.trees')

        with open(astral_input_file, 'wb') as wfd:
            for f in expanded_trees:
                with open(f, 'rb') as fd:
                    shutil.copyfileobj(fd, wfd)

        astral_output_file, astral_log_file, astral_const_file = [dict(), dict(), dict()]
        astral_const_file['incremental'] = join(partition_output_dir, 'astral_constraint.nwk')
        astral_const_file['updates'] = join(partition_output_dir, 'raxml_constraint.nwk')

        for mtd in ['incremental', 'updates']:
            astral_output_file[mtd] = Path(join(partition_output_dir, 'astral_output.%s.nwk' % mtd))
            if (
                mtd == 'updates'
                and not Path(astral_const_file['updates']).is_file()
                and not Path(astral_const_file['incremental']).is_file()
            ):
                shutil.copyfile(astral_output_file['incremental'], astral_output_file['updates'])
                break
            astral_log_file[mtd] = join(partition_output_dir, 'astral.%s.log' % mtd)
            if not Path(astral_const_file[mtd]).is_file():
                s = [
                    'java',
                    '-Xmx%sM' % cls.options.memory,
                    '-Djava.library.path=%s' % cls.astral_libdir,
                    '-jar',
                    cls.astral_mp_exec,
                    '-i',
                    astral_input_file,
                    '-o',
                    astral_output_file[mtd],
                    '-C',
                    '-T',
                    str(cls.options.num_thread),
                ]
            else:
                s = [
                    'java',
                    '-Xmx%sM' % cls.options.memory,
                    '-Djava.library.path=%s' % cls.astral_libdir,
                    '-jar',
                    cls.astral_mp_exec,
                    '-i',
                    astral_input_file,
                    '-o',
                    astral_output_file[mtd],
                    '-j',
                    astral_const_file[mtd],
                    '-C',
                    '-T',
                    str(cls.options.num_thread),
                ]

            with open(astral_log_file[mtd], 'w') as lg:
                with Popen(s, stdout=PIPE, stdin=PIPE, stderr=lg) as p:
                    astral_stdout = p.stdout.read().decode('utf-8')
                    p.poll()
                    if p.returncode:
                        print(
                            'ASTRAL job on partition %s has failed. Check the log file %s for further information.'
                            % (partition_output_dir, astral_log_file[mtd]),
                            file=stderr,
                            flush=True,
                        )
                        exit(p.returncode)
                    # print(astral_stdout)
        # if cls.options.use_gpu:
        #     gpu_opt = ""
        # else:
        #     gpu_opt = "-C"

        # s = f'cp {astral_const_file} {astral_output_file}\n'
        # s = ["cp", astral_const_file, astral_output_file]
