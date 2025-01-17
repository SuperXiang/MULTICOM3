import copy
import os
import sys
import time, json
from bml_casp15.common.util import makedir_if_not_exists, check_dirs
import pandas as pd
from multiprocessing import Pool
import dataclasses
from bml_casp15.tool.foldseek import *
import pickle
import numpy as np
from bml_casp15.complex_templates_search.sequence_based_pipeline import assess_hhsearch_hit, PrefilterError
from bml_casp15.complex_templates_search.parsers import TemplateHit
from bml_casp15.monomer_structure_refinement.util import *


class Monomer_iterative_refinement_pipeline:

    def __init__(self, params, max_template_count=500):

        self.params = params

        self.max_iteration = 5

        self.max_template_count = max_template_count

    def search_templates(self, inpdb, outdir):
        makedir_if_not_exists(outdir)
        foldseek_program = self.params['foldseek_program']
        foldseek_pdb_database = self.params['foldseek_pdb_database']
        foldseek_af_database = self.params['foldseek_af_database']
        foldseek_runner = Foldseek(binary_path=foldseek_program,
                                   databases=[foldseek_pdb_database, foldseek_af_database])
        return foldseek_runner.query(pdb=inpdb, outdir=outdir, progressive_threshold=1)

    def check_and_rank_templates(self, template_result, outfile, query_sequence):

        global_alignment = False

        templates = template_result['local_alignment']
        if len(templates) == 0:
            templates = template_result['global_alignment']
            global_alignment = True

        sort_indices = []
        for i in range(len(templates)):
            target = templates.loc[i, 'target']
            evalue = float(templates.loc[i, 'evalue'])
            if target.find('.atom.gz') > 0:
                if global_alignment and evalue >= 0.8:
                    sort_indices += [i]
                if not global_alignment and evalue < 1e-10:
                    sort_indices += [i]
        for i in range(len(templates)):
            if i in sort_indices:
                continue
            sort_indices += [i]

        keep_indices = []
        for i in sort_indices:
            hit = TemplateHit(index=i,
                              name=templates.loc[i, 'target'].split('.')[0],
                              aligned_cols=int(templates.loc[i, 'alnlen']),
                              query=templates.loc[i, 'qaln'],
                              hit_sequence=templates.loc[i, 'taln'],
                              indices_query=build_alignment_indices(templates.loc[i, 'qaln'],
                                                                    templates.loc[i, 'qstart']),
                              indices_hit=build_alignment_indices(templates.loc[i, 'taln'],
                                                                  templates.loc[i, 'tstart']),
                              sum_probs=0.0)
            try:
                assess_hhsearch_hit(hit=hit, query_sequence=query_sequence)
            except PrefilterError as e:
                msg = f'hit {hit.name.split()[0]} did not pass prefilter: {str(e)}'
                print(msg)
                continue
            keep_indices += [i]
            if len(keep_indices) > self.max_template_count:
                break

        if len(keep_indices) == 0:
            return False

        templates_sorted = copy.deepcopy(templates.iloc[keep_indices])
        templates_sorted.drop(templates_sorted.filter(regex="Unnamed"), axis=1, inplace=True)
        templates_sorted.reset_index(inplace=True, drop=True)
        templates_sorted.to_csv(outfile, sep='\t')
        return True

    def generate_msa_from_templates(self, fasta_file, start_msa, template_file, outfile):
        targetname = None
        seq = None
        for line in open(fasta_file):
            line = line.rstrip('\n')
            if line.startswith('>'):
                targetname = line[1:]
            else:
                seq = line

        templates = pd.read_csv(template_file, sep='\t')

        alignments = {targetname: seq}
        seen_seq = []
        for i in range(len(templates)):
            target = templates.loc[i, 'target']
            qaln = templates.loc[i, 'qaln']
            qstart = int(templates.loc[i, 'qstart'])
            qend = int(templates.loc[i, 'qend'])
            taln = templates.loc[i, 'taln']
            tstart = templates.loc[i, 'tstart']
            tend = templates.loc[i, 'tend']

            query_non_gaps = [res != '-' for res in qaln]
            out_sequence = ''.join(convert_taln_seq_to_a3m(query_non_gaps, taln))

            aln_full = ['-'] * len(seq)
            aln_full[qstart - 1:qend] = out_sequence
            taln_full_seq = ''.join(aln_full)
            if taln_full_seq in seen_seq:
                continue
            alignments[target] = taln_full_seq
            seen_seq += [taln_full_seq]

        fasta_chunks = (f">{k}\n{alignments[k]}" for k in alignments)

        with open(outfile + '.temp', 'w') as fw:
            fw.write('\n'.join(fasta_chunks) + '\n')

        combine_a3ms([start_msa, f"{outfile}.temp"], f"{outfile}.comb")

        cmd = f"{self.params['hhfilter_program']} -diff 50000 -i {outfile}.comb -o {outfile} -id 90"

        os.system(cmd)

    def copy_atoms_and_unzip(self, template_csv, outdir):
        os.chdir(outdir)
        templates = pd.read_csv(template_csv, sep='\t')
        for i in range(len(templates)):
            template_pdb = templates.loc[i, 'target']
            if template_pdb.find('.pdb.gz') > 0:
                os.system(f"cp {self.params['foldseek_af_database_dir']}/{template_pdb} {outdir}")
            else:
                os.system(f"cp {self.params['foldseek_pdb_database_dir']}/{template_pdb} {outdir}")
            os.system(f"gunzip -f {template_pdb}")

    def search(self, fasta_file, input_pdb_dir, outdir, native_pdb=""):

        input_pdb_dir = os.path.abspath(input_pdb_dir)

        fasta_file = os.path.abspath(fasta_file)

        query_sequence = ""
        for line in open(fasta_file):
            line = line.rstrip('\n')
            if line.startswith('>'):
                continue
            else:
                query_sequence = line

        targetname = pathlib.Path(fasta_file).stem

        outdir = os.path.abspath(outdir) + "/"

        makedir_if_not_exists(outdir)

        iteration_scores = {}

        true_tm_scores = {}

        iteration_result_all = {'targetname': [],
                                'model': [],
                                'start_lddt': [],
                                'end_lddt': [],
                                'start_tmscore': [],
                                'end_tmscore': []}

        iteration_result_avg = {'targetname': [targetname], 'start_lddt': [], 'end_lddt': [], 'start_tmscore': [],
                                'end_tmscore': []}

        iteration_result_max = {'targetname': [targetname], 'start_lddt': [], 'end_lddt': [], 'start_tmscore': [],
                                'end_tmscore': []}

        cwd = os.getcwd()

        for i in range(0, 5):
            model_outdir = f"{outdir}/ranked_{i}"
            makedir_if_not_exists(model_outdir)

            current_ref_dir = input_pdb_dir
            ref_start_pdb = f"ranked_{i}.pdb"
            ref_start_ranking_json_file = f"ranking_debug.json"

            model_iteration_scores = []
            model_iteration_tmscores = []

            print(f"Start to refine {ref_start_pdb}")

            for num_iteration in range(self.max_iteration):
                os.chdir(cwd)
                current_work_dir = f"{model_outdir}/iteration{num_iteration + 1}"
                makedir_if_not_exists(current_work_dir)

                start_pdb = f"{current_work_dir}/start.pdb"
                start_msa = f"{current_work_dir}/start.a3m"
                start_ranking_json_file = f"{current_work_dir}/start_ranking.json"

                os.system(f"cp {current_ref_dir}/{ref_start_pdb} {start_pdb}")
                os.system(f"cp {current_ref_dir}/{ref_start_ranking_json_file} {start_ranking_json_file}")
                os.system(f"cp {current_ref_dir}/msas/final.a3m {start_msa}")

                ranking_json = json.loads(open(start_ranking_json_file).read())

                if num_iteration == 0:
                    ref_avg_lddt = ranking_json["plddts"][list(ranking_json["order"])[i]]
                else:
                    ref_avg_lddt = ranking_json["plddts"][list(ranking_json["order"])[0]]

                ref_tmscore = 0
                if os.path.exists(native_pdb):
                    ref_tmscore, _ = cal_tmscore(self.params['tmscore_program'], start_pdb, native_pdb, current_work_dir + '/tmp')

                model_iteration_scores += [ref_avg_lddt]
                model_iteration_tmscores += [ref_tmscore]

                out_model_dir = f"{current_work_dir}/alphafold"
                if not complete_result(out_model_dir):

                    foldseek_res = self.search_templates(inpdb=start_pdb, outdir=current_work_dir + '/foldseek')

                    if not self.check_and_rank_templates(foldseek_res, f"{current_work_dir}/structure_templates.csv", query_sequence):
                        print(f"Cannot find any templates in iteration {num_iteration + 1}")
                        break

                    self.generate_msa_from_templates(fasta_file=fasta_file,
                                                     template_file=f"{current_work_dir}/structure_templates.csv",
                                                     start_msa=start_msa,
                                                     outfile=f"{current_work_dir}/iteration{num_iteration + 1}.a3m")

                    out_template_dir = f"{current_work_dir}/template_pdbs"
                    makedir_if_not_exists(out_template_dir)
                    self.copy_atoms_and_unzip(template_csv=f"{current_work_dir}/structure_templates.csv",
                                              outdir=out_template_dir)

                    makedir_if_not_exists(out_model_dir)
                    cmd = f"python run_alphafold_custom_sim.py " \
                          f"--fasta_path {fasta_file} " \
                          f"--env_dir {self.params['alphafold_env_dir']} " \
                          f"--database_dir {self.params['alphafold_database_dir']} " \
                          f"--custom_msa {current_work_dir}/iteration{num_iteration + 1}.a3m " \
                          f"--temp_struct_csv {current_work_dir}/structure_templates.csv " \
                          f"--struct_atom_dir {out_template_dir} " \
                          f"--output_dir {out_model_dir}"

                    try:
                        os.chdir(self.params['alphafold_program_dir'])
                        os.system(cmd)
                    except Exception as e:
                        print(e)

                new_ranking_json_file = f"{out_model_dir}/ranking_debug.json"
                new_ranking_json = json.loads(open(new_ranking_json_file).read())
                max_lddt_score = new_ranking_json["plddts"][list(new_ranking_json["order"])[0]]

                print(f'#########Iteration: {num_iteration + 1}#############')
                print(f"plddt before: {ref_avg_lddt}")
                print(f"plddt after: {max_lddt_score}")
                if max_lddt_score > ref_avg_lddt:
                    print("Continue to refine")
                    current_ref_dir = out_model_dir
                    ref_start_pdb = f"ranked_0.pdb"
                    ref_start_ranking_json_file = f"ranking_debug.json"
                    print('##################################################')
                    if num_iteration + 1 >= self.max_iteration:
                        print("Reach maximum iteration")
                        ranking_json = json.loads(open(out_model_dir + '/ranking_debug.json').read())
                        ref_avg_lddt = ranking_json["plddts"][list(ranking_json["order"])[0]]

                        ref_tmscore = 0
                        if os.path.exists(native_pdb):
                            ref_tmscore, _ = cal_tmscore(self.params['tmscore_program'],
                                                          out_model_dir + '/' + ref_start_pdb, native_pdb,
                                                          current_work_dir + '/tmp')
                        model_iteration_scores += [ref_avg_lddt]
                        model_iteration_tmscores += [ref_tmscore]
                else:
                    # keep the models in iteration 1 even through the plddt score decreases
                    if num_iteration == 0:
                        ref_start_pdb = f"ranked_0.pdb"
                        ranking_json = json.loads(open(out_model_dir + '/ranking_debug.json').read())
                        ref_avg_lddt = ranking_json["plddts"][list(ranking_json["order"])[0]]

                        ref_tmscore = 0
                        if os.path.exists(native_pdb):
                            ref_tmscore, _ = cal_tmscore(self.params['tmscore_program'],
                                                       out_model_dir + '/' + ref_start_pdb, native_pdb, current_work_dir + '/tmp')
                        model_iteration_scores += [ref_avg_lddt]
                        model_iteration_tmscores += [ref_tmscore]
                    break

            # model_iteration_scores += [max_lddt_score]

            if len(model_iteration_scores) > 0:
                iteration_result_all['targetname'] += [targetname]
                iteration_result_all['model'] += [i]
                iteration_result_all['start_lddt'] += [model_iteration_scores[0]]
                iteration_result_all['end_lddt'] += [model_iteration_scores[len(model_iteration_scores) - 1]]
                iteration_result_all['start_tmscore'] += [model_iteration_tmscores[0]]
                iteration_result_all['end_tmscore'] += [model_iteration_tmscores[len(model_iteration_tmscores) - 1]]

            while len(model_iteration_scores) <= self.max_iteration:
                model_iteration_scores += [0]

            while len(model_iteration_tmscores) <= self.max_iteration:
                model_iteration_tmscores += [0]

            iteration_scores[f'model{i + 1}'] = model_iteration_scores
            true_tm_scores[f'model{i + 1}'] = model_iteration_tmscores

        iteration_result_avg['start_lddt'] = [np.mean(np.array(iteration_result_all['start_lddt']))]
        iteration_result_avg['end_lddt'] = [np.mean(np.array(iteration_result_all['end_lddt']))]
        iteration_result_avg['start_tmscore'] = [np.mean(np.array(iteration_result_all['start_tmscore']))]
        iteration_result_avg['end_tmscore'] = [np.mean(np.array(iteration_result_all['end_tmscore']))]

        iteration_result_max['start_lddt'] = [np.max(np.array(iteration_result_all['start_lddt']))]
        iteration_result_max['end_lddt'] = [np.max(np.array(iteration_result_all['end_lddt']))]
        iteration_result_max['start_tmscore'] = [np.max(np.array(iteration_result_all['start_tmscore']))]
        iteration_result_max['end_tmscore'] = [np.max(np.array(iteration_result_all['end_tmscore']))]

        print(iteration_scores)
        df = pd.DataFrame(iteration_scores)
        df.to_csv(outdir + '/summary.csv')

        df = pd.DataFrame(true_tm_scores)
        df.to_csv(outdir + '/tmscores.csv')

        df = pd.DataFrame(iteration_result_avg)
        df.to_csv(outdir + '/iteration_result_avg.csv')

        df = pd.DataFrame(iteration_result_all)
        df.to_csv(outdir + '/iteration_result_all.csv')

        df = pd.DataFrame(iteration_result_max)
        df.to_csv(outdir + '/iteration_result_max.csv')

        os.chdir(cwd)

        return iteration_result_all, iteration_result_avg, iteration_result_max
