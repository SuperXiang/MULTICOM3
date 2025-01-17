import os, sys, argparse, time
from multiprocessing import Pool
from tqdm import tqdm
from bml_casp15.common.util import is_file, is_dir, makedir_if_not_exists, check_contents, read_option_file, check_dirs
from bml_casp15.monomer_alignment_generation.alignment import *
from bml_casp15.monomer_alignment_generation.rosettafold_msa_runner import *
from bml_casp15.monomer_alignment_generation.colabfold_msa_runner import *
from bml_casp15.monomer_alignment_generation.img_msa_runner import *
from bml_casp15.tool import hhblits
from bml_casp15.tool import jackhmmer


def run_msa_tool(inparams):
    msa_runner, input_fasta_path, msa_out_path, msa_out_name, msa_key = inparams
    """Runs an MSA tool, checking if output already exists first."""
    if not os.path.exists(msa_out_path + '/' + msa_out_name) or len(open(msa_out_path + '/' + msa_out_name).readlines()) == 0:
        workdir = msa_out_path + '/' + msa_key
        makedir_if_not_exists(workdir)
        result = msa_runner.query(input_fasta_path, workdir + '/' + msa_out_name)
        os.system(f"cp {workdir}/{msa_out_name} {msa_out_path}/{msa_out_name}")
    return msa_key, msa_out_path + '/' + msa_out_name


class Monomer_alignment_generation_pipeline:
    """Runs the alignment tools and assembles the input features."""

    def __init__(self,
                 jackhmmer_binary_path,
                 hhblits_binary_path,
                 colabfold_search_binary,
                 colabfold_split_msas_binary,
                 mmseq_binary,
                 uniref90_database_path,
                 uniref90_database_path_new,
                 mgnify_database_path,
                 small_bfd_database_path,
                 bfd_database_path,
                 uniref30_database_path,
                 uniref30_database_path_new,
                 uniclust30_database_path,
                 uniprot_database_path,
                 uniprot_database_path_new,
                 colabfold_databases,
                 hhfilter_binary_path="",
                 mgnify_max_hits: int = 501,
                 uniref_max_hits: int = 10000,
                 use_precomputed_msas: bool = False):
        """Initializes the data pipeline."""

        # alignment generation pipeline from alphafold

        self.jackhmmer_uniref90_runner = None
        self.hhblits_bfd_runner = None
        self.hhblits_uniref_runner = None
        self.jackhmmer_mgnify_runner = None
        self.hhblits_uniclust_runner = None
        self.jackhmmer_uniprot_runner = None
        self.hhblits_uniclust_folddock_runner = None
        self.jackhmmer_small_bfd_runner = None
        self.rosettafold_msa_runner = None
        self.colabfold_msa_runner = None
        self.uniref30_bfd_msa_runner = None
        self.unclust30_bfd_msa_runner = None

        self.hhblits_uniref_runner_new = None
        self.uniref30_bfd_msa_runner_new = None
        self.jackhmmer_uniprot_runner_new = None
        self.jackhmmer_uniref90_runner_new = None

        if len(uniref90_database_path) > 0:
            self.jackhmmer_uniref90_runner = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=uniref90_database_path,
                get_tblout=True)

        if len(uniref90_database_path_new) > 0:
            self.jackhmmer_uniref90_runner_new = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=uniref90_database_path_new,
                get_tblout=True)

        if len(bfd_database_path) > 0:
            self.hhblits_bfd_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[bfd_database_path])

        if len(uniref30_database_path) > 0:
            self.hhblits_uniref_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[uniref30_database_path])

        if len(uniref30_database_path_new) > 0:
            self.hhblits_uniref_runner_new = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[uniref30_database_path_new])

        if len(mgnify_database_path) > 0:
            self.jackhmmer_mgnify_runner = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=mgnify_database_path,
                get_tblout=True)

        if len(uniclust30_database_path) > 0:
            self.hhblits_uniclust_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[uniclust30_database_path])

        if len(uniprot_database_path) > 0:
            self.jackhmmer_uniprot_runner = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=uniprot_database_path,
                get_tblout=True)

        if len(uniprot_database_path_new) > 0:
            self.jackhmmer_uniprot_runner_new = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=uniprot_database_path_new,
                get_tblout=True)

        # if os.path.exists(uniclust30_database_path):
        #     self.hhblits_uniclust_folddock_runner = hhblits.HHBlits(
        #         binary_path=hhblits_binary_path,
        #         databases=[uniclust30_database_path],
        #         all_seqs=True)

        if len(small_bfd_database_path) > 0:
            self.jackhmmer_small_bfd_runner = jackhmmer.Jackhmmer(
                binary_path=jackhmmer_binary_path,
                database_path=small_bfd_database_path,
                get_tblout=True)

        # if len(uniref30_database_path) > 0 and len(bfd_database_path) > 0:
        #     self.rosettafold_msa_runner = RosettaFold_Msa_runner(
        #         hhblits_binary_path=hhblits_binary_path,
        #         hhfilter_binary_path=hhfilter_binary_path,
        #         uniref30_database_path=uniref30_database_path,
        #         bfd_database_path=bfd_database_path)

        if len(uniclust30_database_path) > 0 and len(bfd_database_path) > 0:
            self.unclust30_bfd_msa_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[bfd_database_path, uniclust30_database_path])

        if len(uniref30_database_path) > 0 and len(bfd_database_path) > 0:
            self.uniref30_bfd_msa_runner = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[bfd_database_path, uniref30_database_path])

        if len(uniref30_database_path_new) > 0 and len(bfd_database_path) > 0:
            self.uniref30_bfd_msa_runner_new = hhblits.HHBlits(
                binary_path=hhblits_binary_path,
                databases=[bfd_database_path, uniref30_database_path_new])

        if len(colabfold_databases) > 0:
            self.colabfold_msa_runner = ColabFold_Msa_runner(colabfold_search_binary_path=colabfold_search_binary,
                                                             colabfold_split_msas_binary_path=colabfold_split_msas_binary,
                                                             mmseq_binary_path=mmseq_binary,
                                                             colabfold_databases=colabfold_databases)

    def process(self, input_fasta_path, msa_output_dir, multiprocess=True):
        """Runs alignment tools on the input sequence and creates features."""

        os.system(f"cp {input_fasta_path} {msa_output_dir}")
        input_id = open(input_fasta_path).readlines()[0].rstrip('\n').lstrip('>')

        msa_process_list = []

        if self.jackhmmer_uniref90_runner is not None:
            msa_process_list.append(
                [self.jackhmmer_uniref90_runner, input_fasta_path,
                 msa_output_dir, f'{input_id}_uniref90.sto', 'uniref90_sto'])

        if self.jackhmmer_uniref90_runner_new is not None:
            msa_process_list.append(
                [self.jackhmmer_uniref90_runner_new, input_fasta_path,
                 msa_output_dir, f'{input_id}_uniref90_new.sto', 'uniref90_sto_new'])

        if self.jackhmmer_mgnify_runner is not None:
            msa_process_list.append([self.jackhmmer_mgnify_runner, input_fasta_path,
                                     msa_output_dir, f'{input_id}_mgnify.sto', 'mgnify_sto'])

        if self.jackhmmer_small_bfd_runner is not None:
            msa_process_list.append(
                [self.jackhmmer_small_bfd_runner, input_fasta_path, msa_output_dir,
                 f'{input_id}_smallbfd.sto', 'smallbfd_sto'])

        if self.hhblits_bfd_runner is not None:
            msa_process_list.append([self.hhblits_bfd_runner, input_fasta_path,
                                     msa_output_dir, f'{input_id}_bfd.a3m', 'bfd_a3m'])

        if self.hhblits_uniref_runner is not None:
            msa_process_list.append([self.hhblits_uniref_runner, input_fasta_path,
                                     msa_output_dir, f'{input_id}_uniref30.a3m', 'uniref30_a3m'])

        if self.hhblits_uniref_runner_new is not None:
            msa_process_list.append([self.hhblits_uniref_runner_new, input_fasta_path,
                                     msa_output_dir, f'{input_id}_uniref30_22.a3m', 'uniref30_a3m_22'])

        if self.hhblits_uniclust_runner is not None:
            msa_process_list.append(
                [self.hhblits_uniclust_runner, input_fasta_path,
                 msa_output_dir, f'{input_id}_uniclust30.a3m', 'uniclust30_a3m'])

        if self.hhblits_uniclust_folddock_runner is not None:
            msa_process_list.append([self.hhblits_uniclust_folddock_runner, input_fasta_path,
                                    msa_output_dir, f'{input_id}_uniclust30_all.a3m', 'uniclust30_all_a3m'])

        if self.jackhmmer_uniprot_runner is not None:
            msa_process_list.append([self.jackhmmer_uniprot_runner, input_fasta_path,
                                    msa_output_dir, f'{input_id}_uniprot.sto', 'uniprot_sto'])

        if self.jackhmmer_uniprot_runner_new is not None:
            msa_process_list.append([self.jackhmmer_uniprot_runner_new, input_fasta_path,
                                     msa_output_dir, f'{input_id}_uniprot_new.sto', 'uniprot_sto_new'])

        if self.rosettafold_msa_runner is not None:
            msa_process_list.append(
                [self.rosettafold_msa_runner, input_fasta_path,
                msa_output_dir, f'{input_id}_rosettafold.a3m', 'rosettafold_sto'])

        if self.colabfold_msa_runner is not None:
            msa_process_list.append(
                [self.colabfold_msa_runner, input_fasta_path, msa_output_dir,
                 f'{input_id}_colabfold.a3m', 'colabfold_a3m'])

        if self.unclust30_bfd_msa_runner is not None:
            msa_process_list.append(
                [self.unclust30_bfd_msa_runner, input_fasta_path, msa_output_dir,
                 f'{input_id}_uniclust30_bfd.a3m', 'uniclust30_bfd_a3m'])

        if self.uniref30_bfd_msa_runner is not None:
            msa_process_list.append(
                [self.uniref30_bfd_msa_runner, input_fasta_path,
                 msa_output_dir, f'{input_id}_uniref30_bfd.a3m', 'uniref30_bfd_a3m'])

        if self.uniref30_bfd_msa_runner_new is not None:
            msa_process_list.append(
                [self.uniref30_bfd_msa_runner_new, input_fasta_path,
                 msa_output_dir, f'{input_id}_uniref30_22_bfd.a3m', 'uniref30_22_bfd_a3m'])

        if multiprocess:
            pool = Pool(processes=len(msa_process_list))
            results = pool.map(run_msa_tool, msa_process_list)
            pool.close()
            pool.join()
            #
            result_dict = {}
            for result in results:
                msa_key, msa_out_path = result
                if os.path.exists(msa_out_path):
                    result_dict[msa_key] = msa_out_path
        else:
            result_dict = {}
            for msa_process_params in msa_process_list:
                msa_key, msa_out_path = run_msa_tool(msa_process_params)
                if os.path.exists(msa_out_path):
                    result_dict[msa_key] = msa_out_path

        return result_dict


class Monomer_alignment_generation_pipeline_img:
    """Runs the alignment tools and assembles the input features."""

    def __init__(self,
                 deepmsa_binary_path,
                 bfd_database_path,
                 img_database_path,
                 metaclust_database_path,
                 mgnify_database_path,
                 uniref90_database_path):
        """Initializes the data pipeline."""

        # alignment generation pipeline from alphafold
        self.img_msa_runner = IMG_Msa_runner(binary_path=deepmsa_binary_path,
                                             bfd_database_path=bfd_database_path,
                                             img_database_path=img_database_path,
                                             metaclust_database_path=metaclust_database_path,
                                             mgnify_database_path=mgnify_database_path,
                                             uniref90_database_path=uniref90_database_path)

    def process(self, input_fasta_path, msa_output_dir):
        """Runs alignment tools on the input sequence and creates features."""

        input_id = open(input_fasta_path).readlines()[0].rstrip('\n').lstrip('>')

        img_out_path = os.path.join(msa_output_dir, f'{input_id}.a3m')
        print(img_out_path)

        if not os.path.exists(img_out_path) or len(open(img_out_path).readlines()) == 0:
            img_out_path = self.img_msa_runner.query(input_fasta_path, msa_output_dir)

        return img_out_path
