import os, sys, argparse, time
from multiprocessing import Pool
from bml_casp15.common.util import check_file, check_dir, check_dirs, makedir_if_not_exists, check_contents, \
    read_option_file
from bml_casp15.monomer_alignment_generation.alignment import write_fasta
from bml_casp15.common.protein import read_qa_txt_as_df, parse_fasta, complete_result, make_chain_id_map
from bml_casp15.quaternary_structure_refinement import iterative_refine_pipeline_multimer
from bml_casp15.monomer_structure_refinement import iterative_refine_pipeline
from bml_casp15.common.pipeline import run_monomer_msa_pipeline, run_monomer_template_search_pipeline, \
    run_monomer_structure_generation_pipeline, run_monomer_evaluation_pipeline, run_monomer_refinement_pipeline, \
    run_concatenate_dimer_msas_pipeline, run_complex_template_search_pipeline, run_quaternary_structure_generation_pipeline, \
    run_quaternary_structure_generation_pipeline_foldseek, run_multimer_refinement_pipeline, \
    run_multimer_evaluation_pipeline, run_monomer_msa_pipeline_img, foldseek_iterative_monomer_input

from absl import flags
from absl import app
import copy

flags.DEFINE_string('option_file', None, 'option file')
flags.DEFINE_string('fasta_path', None, 'Path to multimer fastas')
flags.DEFINE_string('output_dir', None, 'Output directory')
FLAGS = flags.FLAGS


def main(argv):
    if len(argv) > 1:
        raise app.UsageError('Too many command-line arguments.')

    check_file(FLAGS.option_file)

    params = read_option_file(FLAGS.option_file)

    makedir_if_not_exists(FLAGS.output_dir)

    check_dirs(params, ['hhblits_program', 'jackhmmer_program'], isdir=False)

    check_file(FLAGS.fasta_path)

    makedir_if_not_exists(FLAGS.output_dir)

    N1_outdir = FLAGS.output_dir + '/N1_monomer_alignments_generation'
    N1_outdir_img = FLAGS.output_dir + '/N1_monomer_alignments_generation_img'
    N2_outdir = FLAGS.output_dir + '/N2_monomer_template_search'
    N3_outdir = FLAGS.output_dir + '/N3_monomer_structure_generation'
    img_msas = {}

    print("#################################################################################################")

    print("#################################################################################################")
    print("1-3. Start to generate monomer models")

    makedir_if_not_exists(N1_outdir)

    with open(FLAGS.fasta_path) as f:
        input_fasta_str = f.read()
    input_seqs, input_descs = parse_fasta(input_fasta_str)
    chain_id_map, chain_id_seq_map = make_chain_id_map(sequences=input_seqs,
                                                       descriptions=input_descs)

    for chain_id in chain_id_map:
        monomer_id = chain_id_map[chain_id].description
        monomer_sequence = chain_id_map[chain_id].sequence

        with open(f"{FLAGS.output_dir}/{monomer_id}.fasta", "w") as fw:
            write_fasta({monomer_id: monomer_sequence}, fw)
        N1_monomer_outdir = N1_outdir + '/' + monomer_id
        makedir_if_not_exists(N1_monomer_outdir)
        result = run_monomer_msa_pipeline(f"{FLAGS.output_dir}/{monomer_id}.fasta", N1_monomer_outdir, params)
        if result is None:
            raise RuntimeError(f"Program failed in step 1: monomer {monomer_id} alignment generation")

        N1_monomer_outdir_img = N1_outdir_img + '/' + monomer_id
        makedir_if_not_exists(N1_monomer_outdir_img)
        # monomer_msa_pipeline_img = Monomer_alignment_generation_pipeline_img(deepmsa_binary_path=params['deepmsa2_program'],
        #                                                                      bfd_database_path=params['bfd_database'],
        #                                                                      img_database_path=params['img_database'],
        #                                                                      metaclust_database_path=params[
        #                                                                          'metaclust_database'],
        #                                                                      mgnify_database_path=params[
        #                                                                          'mgnify_database'],
        #                                                                      uniref90_database_path=params[
        #                                                                          'uniref90_fasta'])

        # img_msas[chain_id] = monomer_msa_pipeline_img.process(f"{FLAGS.output_dir}/{monomer_id}.fasta", N1_monomer_outdir_img)

        N2_monomer_outdir = N2_outdir + '/' + monomer_id
        makedir_if_not_exists(N2_monomer_outdir)
        template_file = run_monomer_template_search_pipeline(targetname=monomer_id, sequence=monomer_id,
                                                             a3m=f"{N1_monomer_outdir}/{monomer_id}_uniref90.sto",
                                                             outdir=N2_monomer_outdir, params=params)
        if template_file is None:
            raise RuntimeError(f"Program failed in step 2: monomer {monomer_id} template search")

        N3_monomer_outdir = N3_outdir + '/' + monomer_id
        makedir_if_not_exists(N3_monomer_outdir)
        if not run_monomer_structure_generation_pipeline(params=params,
                                                         run_methods=['default',  # 'default+seq_template',
                                                                      'original',  # 'original+seq_template'
                                                                      'colabfold'],  # , 'colabfold+seq_template'],
                                                         fasta_path=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                                                         alndir=N1_monomer_outdir,
                                                         templatedir=N2_monomer_outdir,
                                                         outdir=N3_monomer_outdir):
            print(f"Program failed in step 3: monomer {monomer_id} structure generation")

    print("#################################################################################################")

    print("#################################################################################################")
    print("4. Start to generate complex alignments")

    N4_outdir = FLAGS.output_dir + '/N4_complex_alignments_concatenation'
    makedir_if_not_exists(N4_outdir)

    # try:
    #     run_concatenate_dimer_msas_pipeline(
    #         multimer='_'.join([chain_id_map[chain_id].description for chain_id in chain_id_map]),
    #         monomer_aln_dir=N1_outdir, outputdir=N4_outdir, params=params)
    # except Exception as e:
    #     print(e)
    #     print("Program failed in step 4")

    print("#################################################################################################")

    print("#################################################################################################")

    print("5. Start to search complex templates based on monomer structures")

    N5_outdir = FLAGS.output_dir + '/N5_complex_templates_search'

    run_complex_template_search_pipeline(multimers=[chain_id_map[chain_id].description for chain_id in chain_id_map],
                                         monomer_aln_dir=N1_outdir,
                                         monomer_model_dir=N3_outdir,
                                         outdir=N5_outdir, params=params)

    print("#################################################################################################")

    print("#################################################################################################")

    print("6. Start to generate complex quaternary structures")
    N6_outdir = FLAGS.output_dir + '/N6_quaternary_structure_generation'
    makedir_if_not_exists(N6_outdir)

    if not run_quaternary_structure_generation_pipeline(params=params,
                                                        fasta_path=FLAGS.fasta_path,
                                                        chain_id_map=chain_id_map,
                                                        aln_dir=N1_outdir,
                                                        complex_aln_dir=N4_outdir,
                                                        template_dir=N5_outdir,
                                                        monomer_model_dir=N3_outdir,
                                                        output_dir=N6_outdir):
        print("Program failed in step 7")

    print("Complex quaternary structure generation has been finished!")

    print("#################################################################################################")

    print("#################################################################################################")

    print("7. Start to evaluate monomer models")

    N7_outdir = FLAGS.output_dir + '/N7_monomer_structure_evaluation'
    monomer_qas_res = {}
    for chain_id_idx, chain_id in enumerate(chain_id_map):
        monomer_id = chain_id_map[chain_id].description
        N7_monomer_outdir = N7_outdir + '/' + monomer_id
        makedir_if_not_exists(N7_monomer_outdir)
        result = run_monomer_evaluation_pipeline(params=params,
                                                 targetname=monomer_id,
                                                 fasta_file=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                                                 input_monomer_dir=N3_outdir + '/' + monomer_id,
                                                 input_multimer_dir=N6_outdir,
                                                 chainid=chain_id,
                                                 unrelaxed_chainid=chain_id,
                                                 outputdir=N7_monomer_outdir)
        if result is None:
            raise RuntimeError(f"Program failed in step 7: monomer {monomer_id} model evaluation")
        monomer_qas_res[monomer_id] = result

    N8_outdir = FLAGS.output_dir + '/N8_monomer_structure_refinement'
    for chain_id in chain_id_map:
        monomer_id = chain_id_map[chain_id].description
        N8_monomer_outdir = N8_outdir + '/' + monomer_id
        makedir_if_not_exists(N8_monomer_outdir)
        final_dir = N8_monomer_outdir + '/final'

        ref_ranking = read_qa_txt_as_df(
            monomer_qas_res[monomer_id]['apollo'])  # apollo or average ranking or the three qas
        refine_inputs = []
        for i in range(5):
            pdb_name = ref_ranking.loc[i, 'model']
            refine_input = iterative_refine_pipeline.refinement_input(
                fasta_path=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                pdb_path=f"{N7_outdir}/{monomer_id}/pdb/{pdb_name}",
                pkl_path=f"{N7_outdir}/{monomer_id}/pkl/{pdb_name.replace('.pdb', '.pkl')}",
                msa_path=f"{N7_outdir}/{monomer_id}/msa/{pdb_name.replace('.pdb', '.a3m')}")
            refine_inputs += [refine_input]

        run_monomer_refinement_pipeline(params=params, refinement_inputs=refine_inputs,
                                        outdir=N8_monomer_outdir, finaldir=final_dir)

    print("The refinement for the top-ranked monomer models has been finished!")

    print("#################################################################################################")

    print("#################################################################################################")

    print("Check the alignment depth")

    img_wait_list = []
    for chain_id in chain_id_map:
        monomer_id = chain_id_map[chain_id].description
        default_alphafold_msa = N3_outdir + '/' + monomer_id + '/default/msas/monomer_final.a3m'
        if len(open(default_alphafold_msa).readlines()) < 200:
            img_wait_list += [chain_id]

    img_processed_list = []
    while len(img_processed_list) != len(img_wait_list):
        for chain_id in img_wait_list:
            if chain_id in img_processed_list:
                continue
            if os.path.exists(img_msas[chain_id]):
                print("Found img alignment, start to run monomer model generation again")

                N1_monomer_outdir_img = N1_outdir_img + '/' + monomer_id

                if not run_monomer_structure_generation_pipeline(params=params,
                                                                 run_methods=['img', 'img+seq_template'],
                                                                 fasta_path=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                                                                 alndir=N1_monomer_outdir_img,
                                                                 templatedir=N2_outdir + '/' + monomer_id,
                                                                 outdir=N3_outdir + '/' + monomer_id):
                    print("Program failed in step 3: monomer structure generation")

                print("9. Start to evaluate monomer models")

                N9_monomer_outdir = FLAGS.output_dir + '/N9_monomer_structure_evaluation/' + monomer_id

                makedir_if_not_exists(N9_monomer_outdir)

                result = run_monomer_evaluation_pipeline(params=params,
                                                         targetname=monomer_id,
                                                         fasta_file=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                                                         input_monomer_dir=N3_outdir + '/' + monomer_id,
                                                         input_multimer_dir=N6_outdir,
                                                         chainid=chain_id,
                                                         unrelaxed_chainid=chain_id,
                                                         outputdir=N9_monomer_outdir)

                if result is None:
                    raise RuntimeError("Program failed in step 6: monomer model evaluation")

                print("The evaluation for monomer models has been finished!")

                print(
                    "#################################################################################################")

                print(
                    "#################################################################################################")

                print("10. Start to refine monomer models based on the qa rankings")

                N10_monomer_outdir = FLAGS.output_dir + '/N10_monomer_structure_refinement/' + monomer_id

                makedir_if_not_exists(N10_monomer_outdir)

                old_ref_ranking = copy.deepcopy(read_qa_txt(monomer_qas_res[monomer_id]['apollo']))
                monomer_qas_res[monomer_id] = result

                refined_models = [old_ref_ranking.loc[i, 'model'] for i in range(5)]
                ref_ranking = read_qa_txt_as_df(result['apollo'])  # apollo or average ranking or the three qas

                refine_inputs = []
                for i in range(5):
                    pdb_name = ref_ranking.loc[i, 'model']
                    if pdb_name not in refined_models:
                        refine_input = iterative_refine_pipeline.refinement_input(
                            fasta_path=f"{FLAGS.output_dir}/{monomer_id}.fasta",
                            pdb_path=f"{N9_monomer_outdir}/pdb/{pdb_name}",
                            pkl_path=f"{N9_monomer_outdir}/pkl/{pdb_name.replace('.pdb', '.pkl')}",
                            msa_path=f"{N9_monomer_outdir}/msa/{pdb_name.replace('.pdb', '.a3m')}")
                        refine_inputs += [refine_input]
                    else:
                        os.system(f"cp -r {N8_outdir}/{monomer_id}/{pdb_name} {N10_monomer_outdir}")

                print("The refinement for the top-ranked monomer models has been finished!")

                final_dir = N10_monomer_outdir + '/final'
                run_monomer_refinement_pipeline(params=params, refinement_inputs=refine_inputs,
                                                outdir=N10_monomer_outdir, finaldir=final_dir)

                img_processed_list += [chain_id]

                print("The evaluation for monomer models has been finished!")

        # sleep for 5 mins
        time.sleep(300)

    print("10. Start to run multimer iterative generation pipeline using top-ranked monomer models")

    qa_result_dir = FLAGS.output_dir + '/N9_monomer_structure_evaluation/'
    if len(img_wait_list) == 0:
        qa_result_dir = N7_outdir

    pipeline_inputs = []
    for i in range(5):
        monomer_pdb_dirs = {}
        monomer_alphafold_a3ms = {}
        for chain_id in chain_id_map:
            monomer_id = chain_id_map[chain_id].description
            monomer_ranking = read_qa_txt_as_df(monomer_qas_res[monomer_id]['apollo'])
            pdb_name = monomer_ranking.loc[i, 'model']
            monomer_pdb_dirs[chain_id] = f"{qa_result_dir}/{monomer_id}/pdb/{pdb_name}"
            monomer_alphafold_a3ms[chain_id] = f"{qa_result_dir}/{monomer_id}/msa/{pdb_name.replace('.pdb', '.a3m')}"
        pipeline_inputs += [foldseek_iterative_monomer_input(monomer_pdb_dirs=monomer_pdb_dirs,
                                                             monomer_alphafold_a3ms=monomer_alphafold_a3ms)]

    if len(img_wait_list) != 0:
        for chain_id in chain_id_map:
            monomer_id = chain_id_map[chain_id].description
            monomer_pdb_dirs[chain_id] = f"{qa_result_dir}/{monomer_id}/pdb/img_1.pdb"
            monomer_alphafold_a3ms[chain_id] = f"{qa_result_dir}/{monomer_id}/msa/img_1.a3m"
        pipeline_inputs += [foldseek_iterative_monomer_input(monomer_pdb_dirs=monomer_pdb_dirs,
                                                             monomer_alphafold_a3ms=monomer_alphafold_a3ms)]

    if not run_quaternary_structure_generation_pipeline_foldseek(params=params, fasta_path=fasta_path,
                                                                 chain_id_map=chain_id_map,
                                                                 pipeline_inputs=pipeline_inputs, outdir=N6_outdir):
        print("Program failed in step 6 iterative")

    print("Complex quaternary structure generation has been finished!")

    print("#################################################################################################")

    print("#################################################################################################")

    print("8. Start to evaluate multimer models")

    N11_outdir = outdir + '/N11_multimer_structure_evaluation'
    multimer_qa_result = run_multimer_evaluation_pipeline(params=params, indir=N6_outdir, outdir=N11_outdir)

    print("#################################################################################################")

    print("#################################################################################################")

    print("9. Start to refine multimer models based on the qa rankings")

    N12_outdir = outdir + '/N12_multimer_structure_refinement'

    makedir_if_not_exists(N12_outdir)
    ref_ranking = read_qa_txt_as_df(multimer_qa_result['alphafold'])  # apollo or average ranking or the three qas

    refine_inputs = []
    for i in range(5):
        pdb_name = ref_ranking.loc[i, 'model']
        msa_paths = {}
        for chain_id in chain_id_map:
            msa_paths[chain_id] = dict(
                paired_msa=f"{N11_outdir}/msa/{chain_id_map[chain_id].description}/{pdb_name}.multimer.a3m",
                monomer_msa=f"{N11_outdir}/msa/{chain_id_map[chain_id].description}/{pdb_name}.monomer.a3m")

        refine_input = iterative_refine_pipeline_multimer.refinement_input_multimer(chain_id_map=chain_id_map,
                                                                           fasta_path=fasta_path,
                                                                           pdb_path=N11_outdir + '/pdb/' + pdb_name,
                                                                           pkl_path=N11_outdir + '/pkl/' + pdb_name.replace(
                                                                               '.pdb', '.pkl'),
                                                                           msa_paths=msa_paths)
        refine_inputs += [refine_input]

    final_dir = N12_outdir + '/final'
    run_multimer_refinement_pipeline(params=params, refinement_inputs=refine_inputs, outdir=N12_outdir,
                                     finaldir=final_dir)

    print("The refinement for the top-ranked multimer models has been finished!")


if __name__ == '__main__':
    flags.mark_flags_as_required([
        'option_file',
        'fasta_path',
        'output_dir'
    ])
    app.run(main)
