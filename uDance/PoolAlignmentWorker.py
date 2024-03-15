import os
from os.path import join
from pathlib import Path

import numpy as np


class PoolAlignmentWorker:
    subalignment_length = None
    fragment_length = None
    fa_dict = None
    basename = None

    @classmethod
    def set_class_attributes(cls, subalignment_length, fragment_length, fa_dict, basename):
        cls.subalignment_length = subalignment_length
        cls.fragment_length = fragment_length
        cls.fa_dict = fa_dict
        cls.basename = basename

    #  TODO raise error if directory exists
    @classmethod
    def worker(cls, sp_path):
        with open(sp_path) as file:
            species = file.readlines()
            species = set([line.rstrip() for line in species])
        partition_aln = {key: cls.fa_dict[key] for key in species if key in cls.fa_dict}
        partition_output_dir = os.path.dirname(sp_path)
        if len(partition_aln) < 4:
            return None
        aln_length = len(next(iter(partition_aln.values())))
        not_all_gap = np.array([False] * aln_length)
        for s in partition_aln.values():
            not_all_gap = np.logical_or(not_all_gap, (s != b'-'))
        removelist = []
        for k, v in partition_aln.items():
            ungapped = v[not_all_gap]
            if sum(ungapped != b'-') >= 75:
                partition_aln[k] = ungapped
            else:
                removelist.append(k)
        print(
            '%d fragmentary sequences are removed from gene %s on partition %s.'
            % (len(removelist), cls.basename, partition_output_dir)
        )
        for k in removelist:
            partition_aln.pop(k)

        trimmed_aln_length = len(next(iter(partition_aln.values())))

        # deduplicate the alignment
        seq_keyed_dict = {}
        for name, sba in partition_aln.items():
            seq = sba.tostring().decode('UTF-8')
            if seq in seq_keyed_dict:
                seq_keyed_dict[seq].append(name)
            else:
                seq_keyed_dict[seq] = [name]

        seq_keyed_dict = {k: sorted(v) for k, v in seq_keyed_dict.items()}

        if trimmed_aln_length >= cls.subalignment_length and len(seq_keyed_dict) >= 4:
            # write trimmed MSA fasta
            res = []
            duplist = []
            for k, v in sorted(seq_keyed_dict.items()):
                res.append('>' + v[0])
                res.append(k)
                if len(v) > 1:
                    duplist.append('\t'.join(v))

            aln_outdir = join(partition_output_dir, cls.basename)
            Path(aln_outdir).mkdir(parents=True, exist_ok=True)
            aln_output_path = join(aln_outdir, 'aln.fa')
            with open(aln_output_path, 'w', buffering=100000000) as f:
                f.write('\n'.join(res))
                f.write('\n')
            if duplist:
                dupmap_output_path = join(aln_outdir, 'dupmap.txt')
                with open(dupmap_output_path, 'w', buffering=100000000) as f:
                    f.write('\n'.join(duplist))
                    f.write('\n')

            # # create the raxml constraint
            # constraint_outgroup_tree = join(partition_output_dir, "raxml_constraint.nwk")
            # if isfile(constraint_outgroup_tree) and cls.options.constrain_outgroups:
            #     t = ts.read_tree_newick(constraint_outgroup_tree)
            #     induced_constraints_tree = t.extract_tree_with(list(partition_aln.keys()), suppress_unifurcations=True)
            #     induced_constraints_tree.is_rooted = False
            #     numlabels = induced_constraints_tree.num_nodes(internal=False)
            #     if numlabels >= 4:
            #         # write fasttree and raxml constraint
            #         bipartition_path = join(aln_outdir, "bipartition.fasta")
            #         with open(bipartition_path, "w") as f:
            #             f.write(compute_bipartition_alignment(induced_constraints_tree.__str__()))
            #         induced_raxml_constraint_path = join(aln_outdir, "raxml_constraint.nwk")
            #         induced_constraints_tree.write_tree_newick(induced_raxml_constraint_path)
            #         with open(induced_constraints_tree, "a") as a_file:
            #             a_file.write("\n")
            #
            # script = join(aln_outdir, "run.sh")
            # with open(script, "w") as f:
            #     f.write("#!/usr/bin/env bash\n\n")
            #     f.write("export OMP_NUM_THREADS=1\n\n")
            #     raxml_constraint_path = join(partition_output_dir, "raxml_constraint.nwk")
            #     astral_constraint_path = join(partition_output_dir, "astral_constraint.nwk")
            #     if cls.options.method == 'raxml-ng':
            #         # raxml-ng method first estimates a starting tree using fasttree2
            #         bipartition_path = join(aln_outdir, "bipartition.fasta")
            #         fasttree_log = join(aln_outdir, "fasttree.log")
            #         fasttree_err = join(aln_outdir, "fasttree.err")
            #         fasttree_nwk = join(aln_outdir, "fasttree.nwk")
            #         if isfile(bipartition_path) and cls.options.constrain_outgroups:
            #             f.write("FastTreeMP -lg -gamma -constraints %s -log %s < %s > %s 2> %s \n"
            #                     % (bipartition_path, fasttree_log, aln_output_path, fasttree_nwk, fasttree_err))
            #         else:
            #             f.write("FastTreeMP -lg -gamma -log %s < %s > %s 2> %s \n"
            #                     % (fasttree_log, aln_output_path, fasttree_nwk, fasttree_err))
            #
            #         fasttree_resolved_nwk = join(aln_outdir, "fasttree_resolved.nwk")
            #         # random resolution. THIS MAY PREVENT REPRODUCIBILITY!!! Change in the future
            #         f.write("python3 -c \"import sys, treeswift; "
            #                 "t=treeswift.read_tree_newick(input()); "
            #                 "t.resolve_polytomies(); print(t)\" < %s > %s \n" % (fasttree_nwk, fasttree_resolved_nwk))
            #
            #         raxml_err = join(aln_outdir, "raxml.err")
            #         raxml_out = join(aln_outdir, "raxml.out")
            #         raxml_run = join(aln_outdir, "RUN")
            #
            #         if isfile(raxml_constraint_path) and cls.options.constrain_outgroups:
            #             f.write("raxml-ng --tree %s --tree-constraint %s "
            #                     "--msa %s --model LG+G --prefix %s --seed 12345 "
            #                     "--threads 1 > %s 2> %s \n"
            #                     % (fasttree_resolved_nwk, raxml_constraint_path, aln_output_path,
            #                        raxml_run, raxml_out, raxml_err))
            #         else:
            #             f.write("raxml-ng --tree %s "
            #                     "--msa %s --model LG+G --prefix %s --seed 12345 "
            #                     "--threads 1 > %s 2> %s \n"
            #                     % (fasttree_resolved_nwk, aln_output_path,
            #                        raxml_run, raxml_out, raxml_err))
            #
            #     elif cls.options.method == 'iqtree':
            #         iqtree_err = join(aln_outdir, "iqtree.err")
            #         iqtree_out = join(aln_outdir, "iqtree.out")
            #         iqtree_run = join(aln_outdir, "RUN")
            #         iqtree_constraint_path = join(partition_output_dir, "iqtree_constraint.nwk")
            #         if cls.options.protein_seqs:
            #             # f.write("iqtree -t %s -s %s --prefix %s "
            #             #         "--seed 12345 -T 4 --model LG+G > %s 2> %s \n"
            #             #         % (fasttree_resolved_nwk, aln_output_path,
            #             #            iqtree_run, iqtree_out, iqtree_err))
            #             if isfile(raxml_constraint_path) and cls.options.constrain_outgroups:
            #                 shutil.copy(raxml_constraint_path, iqtree_constraint_path)
            #                 f.write("iqtree2 -s %s --prefix %s "
            #                         "--seed 12345 -T AUTO --model LG+G -g %s > %s 2> %s \n"
            #                         % (aln_output_path, iqtree_run,
            #                            iqtree_constraint_path,
            #                            iqtree_out, iqtree_err))
            #             else:
            #                 f.write("iqtree2 -s %s --prefix %s "
            #                         "--seed 12345 -T AUTO --model LG+G > %s 2> %s \n"
            #                         % (aln_output_path, iqtree_run,
            #                            iqtree_out, iqtree_err))
            #         else:
            #             if isfile(raxml_constraint_path) and cls.options.constrain_outgroups:
            #                 shutil.copy(raxml_constraint_path, iqtree_constraint_path)
            #                 f.write("iqtree2 -s %s --prefix %s "
            #                         "--seed 12345 -T AUTO --model GTR+F+G4 -g %s > %s 2> %s \n"
            #                         % (aln_output_path, iqtree_run,
            #                            iqtree_constraint_path,
            #                            iqtree_out, iqtree_err))
            #             else:
            #                 f.write("iqtree2 -s %s --prefix %s "
            #                         "--seed 12345 -T AUTO --model GTR+F+G4 > %s 2> %s \n"
            #                         % (aln_output_path, iqtree_run,
            #                            iqtree_out, iqtree_err))
            #     elif cls.options.method == 'raxml-8':
            #         raxml8_constraint_path = join(partition_output_dir, "raxml8_constraint.nwk")
            #         raxml8_run = "file"
            #         raxml8_err = join(aln_outdir, "raxml8.err")
            #         raxml8_out = join(aln_outdir, "raxml8.out")
            #         if cls.options.num_thread > 1:
            #             executable = "raxmlHPC-PTHREADS"
            #         else:
            #             executable = "raxmlHPC"
            #         if cls.options.protein_seqs:
            #             # f.write("iqtree -t %s -s %s --prefix %s "
            #             #         "--seed 12345 -T 4 --model LG+G > %s 2> %s \n"
            #             #         % (fasttree_resolved_nwk, aln_output_path,
            #             #            iqtree_run, iqtree_out, iqtree_err))
            #             if isfile(raxml_constraint_path) and cls.options.constrain_outgroups:
            #                 shutil.copy(raxml_constraint_path, raxml8_constraint_path)
            #                 f.write("%s -s %s -w %s -n %s "
            #                         "-p 12345 -T %s -m LG+G -g %s > %s 2> %s \n"
            #                         % (executable, aln_output_path, aln_outdir, raxml8_run, min(cls.options.num_thread, 16),
            #                            raxml8_constraint_path, raxml8_out, raxml8_err))
            #             else:
            #                 f.write("%s -s %s -w %s -n %s "
            #                         "-p 12345 -T %s -m LG+G > %s 2> %s \n"
            #                         % (executable, aln_output_path, aln_outdir, raxml8_run, min(cls.options.num_thread, 16),
            #                            raxml8_out, raxml8_err))
            #         else:
            #             if isfile(raxml_constraint_path) and cls.options.constrain_outgroups:
            #                 shutil.copy(raxml_constraint_path, raxml8_constraint_path)
            #                 f.write("%s -s %s -w %s -n %s "
            #                         "-p 12345 -T %s -m GTRCAT -g %s > %s 2> %s \n"
            #                         % (executable, aln_output_path, aln_outdir, raxml8_run, min(cls.options.num_thread, 16),
            #                            raxml8_constraint_path, raxml8_out, raxml8_err))
            #             else:
            #                 f.write("%s -s %s -w %s -n %s "
            #                         "-p 12345 -T %s -m GTRCAT > %s 2> %s \n"
            #                         % (executable, aln_output_path, aln_outdir, raxml8_run, min(cls.options.num_thread, 16),
            #                            raxml8_out, raxml8_err))
            # st = os.stat(script)
            # os.chmod(script, st.st_mode | stat.S_IEXEC)
            # return trimmed_aln_length*len(partition_aln), script
        return None
