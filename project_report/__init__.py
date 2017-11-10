import re
import os
import csv
import yaml
import matplotlib
matplotlib.use('Agg')
import pandas as pd
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import matplotlib.collections as mpcollections

import numpy as np
from dateutil import parser
from os import path, listdir
from collections import OrderedDict
from jinja2 import Environment, FileSystemLoader
from egcg_core.util import find_file
from egcg_core.clarity import connection
from egcg_core.app_logging import logging_default as log_cfg
from config import cfg
from egcg_core.rest_communication import get_documents
from egcg_core.exceptions import EGCGError

app_logger = log_cfg.get_logger(__name__)
log_cfg.get_logger('weasyprint', 40)

try:
    from weasyprint import HTML
    from weasyprint.fonts import FontConfiguration
except ImportError:
    HTML = None

species_alias = {
    'Homo sapiens': 'Human',
    'Human': 'Human'
}

class ProjectReport:
    _lims_samples_for_project = None
    _database_samples_for_project = None
    _project = None

    workflow_alias = {
        'TruSeq Nano DNA Sample Prep': 'truseq_nano',
        'TruSeq PCR-Free DNA Sample Prep': 'truseq_pcrfree',
        'TruSeq PCR-Free Sample Prep': 'truseq_pcrfree',
        'TruSeq DNA PCR-Free Sample Prep': 'truseq_pcrfree'
    }

    def __init__(self, project_name, working_dir=None):
        self.project_name = project_name
        self.working_dir = working_dir or os.getcwd()
        self.project_source = path.join(cfg.query('sample', 'delivery_source'), project_name)
        self.project_delivery = path.join(cfg.query('sample', 'delivery_dest'), project_name)
        self.lims = connection()
        self.params = {
            'project_name': project_name,
            'adapter1': 'AGATCGGAAGAGCACACGTCTGAACTCCAGTCA',
            'adapter2': 'AGATCGGAAGAGCGTCGTGTAGGGAAAGAGTGT'
        }
        self.font_config = FontConfiguration()

    @property
    def project(self):
        if self._project is None:
            self._project = self.lims.get_projects(name=self.project_name)[0]
        return self._project

    @property
    def sample_status(self, sample_id):
        endpoint = 'lims/status/sample_status'
        sample_status = get_documents(endpoint, match={"sample_id": sample_id})
        return sample_status

    @property
    def samples_for_project_lims(self):
        if self._lims_samples_for_project is None:
            self._lims_samples_for_project = self.lims.get_samples(projectname=self.project_name)
        return self._lims_samples_for_project

    @property
    def samples_for_project_restapi(self):
        if self._database_samples_for_project is None:
            self._database_samples_for_project = get_documents('aggregate/samples', match={"project_id": self.project_name, 'delivered': 'yes'}, paginate=False)
            if not self._database_samples_for_project:
                raise EGCGError('No samples found for project %s' % (self.project_name))
        return self._database_samples_for_project

    @property
    def sample_name_delivered(self):
        return [sample.get('sample_id') for sample in self.samples_for_project_restapi]

    def get_lims_sample(self, sample_name):
        samples = [s for s in self.samples_for_project_lims if s.name == sample_name]
        if len(samples) == 1:
            return samples[0]
        raise ValueError('%s samples found for %s' % (len(samples), sample_name))

    def get_all_sample_names(self, modify_names=False):
        if modify_names:
            return [re.sub(r'[: ]', '_', s.name) for s in self.samples_for_project_lims]
        return [s.name for s in self.samples_for_project_lims]

    def get_fluidx_barcode(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('2D Barcode')

    def get_analysis_type_from_sample(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('Analysis Type')

    def get_library_workflow_from_sample(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('Prep Workflow')

    def get_species_from_sample(self, sample_name):
        s = self.get_lims_sample(sample_name).udf.get('Species')
        return species_alias.get(s, s)

    def get_sample_total_dna(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('Total DNA (ng)')

    def get_required_yield(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('Required Yield (Gb)')

    def get_quoted_coverage(self, sample_name):
        return self.get_lims_sample(sample_name).udf.get('Coverage (X)')

    def get_genome_version(self, sample_name):
        s = self.get_lims_sample(sample_name)
        species = self.get_species_from_sample(sample_name)
        genome_version = s.udf.get('Genome Version', None)
        if not genome_version and species:
            return cfg.query('species', species, 'default')
        return genome_version

    def get_species(self, samples):
        species = set()
        for sample in samples:
            species.add(self.get_species_from_sample(sample))
        return species

    def get_species_found(self, sample):
        sample_contamination = sample.get('species_contamination', {}).get('contaminant_unique_mapped')
        if sample_contamination:
            species_found = [s for s in sample_contamination if sample_contamination[s] > 500]
            return species_found
        return None

    def get_library_workflow(self, samples):
        library_workflow = set()
        for sample in samples:
            library_workflow.add(self.get_library_workflow_from_sample(sample))
        if len(library_workflow) != 1:
            raise ValueError('%s workflows used for this project: %s' % (len(library_workflow), library_workflow))
        library_workflow = library_workflow.pop()
        return library_workflow

    def get_analysis_type(self, samples):
        analysis_types = set()
        for sample in samples:
            analysis_types.add(self.get_analysis_type_from_sample(sample))
        if len(analysis_types) != 1:
            raise ValueError('%s analysis type used for this project: %s' % (len(analysis_types), analysis_types))
        return analysis_types.pop()

    def project_size_in_terabytes(self):
        project_size = self.get_folder_size(self.project_delivery)
        return (project_size/1000000000000.0)

    def parse_date(self, date):
        if not date:
            return 'None'
        d = parser.parse(date)
        datelist = [d.year, d.month, d.day]
        return '-'.join([str(i) for i in datelist])


    @staticmethod
    def calculate_mean(values):
        return (sum(values)/max(len(values), 1))

    @property
    def project_title(self):
        return self.project.udf.get('Project Title', '')

    @property
    def quote_number(self):
        return self.project.udf.get('Quote No.', '')

    @property
    def enquiry_number(self):
        return self.project.udf.get('Enquiry Number', '')

    def update_from_program_csv(self, program_csv):
        all_programs = {}
        if program_csv and path.exists(program_csv):
            with open(program_csv) as open_prog:
                for row in csv.reader(open_prog):
                    all_programs[row[0]] = row[1]
        # TODO: change the hardcoded version of bcl2fastq
        all_programs['bcl2fastq'] = '2.17.1.14'
        for p in ['bcl2fastq', 'bcbio', 'bwa', 'gatk', 'samblaster']:
            if p in all_programs:
                self.params[p + '_version'] = all_programs.get(p)

    def update_from_project_summary_yaml(self, summary_yaml):
        with open(summary_yaml, 'r') as open_file:
            full_yaml = yaml.safe_load(open_file)
        sample_yaml = full_yaml['samples'][0]
        self.params['bcbio_version'] = path.basename(path.dirname(sample_yaml['dirs']['galaxy']))
        self.params['genome_version'] = sample_yaml['genome_build']

    def update_from_program_version_yaml(self, prog_vers_yaml):
        with open(prog_vers_yaml, 'r') as open_file:
            full_yaml = yaml.safe_load(open_file)
            for p in ['bcl2fastq', 'bwa', 'gatk', 'samblaster', 'biobambam_sortmapdup']:
                if p in full_yaml:
                    self.params[p + '_version'] = full_yaml.get(p)

    def get_project_info(self):
        sample_names = self.get_all_sample_names()
        genome_versions = set()
        species_submitted = set()
        project = self.lims.get_projects(name=self.project_name)[0]
        library_workflow = self.get_library_workflow(self.sample_name_delivered)
        for sample in self.sample_name_delivered:
            species = self.get_species_from_sample(sample)
            genome_version = self.get_genome_version(sample)
            species_submitted.add(species)
            genome_versions.add(genome_version)

        project_info = (
            ('Project name', self.project_name),
            ('Project title', self.project_title),
            ('Enquiry no', self.enquiry_number),
            ('Quote no', self.quote_number),
            ('Quote contact', '%s %s (%s)' % (project.researcher.first_name,
                                              project.researcher.last_name,
                                              project.researcher.email)),
            ('Number of samples', len(sample_names)),
            ('Number of samples delivered', len(self.samples_for_project_restapi)),
            ('Date samples received', 'Detailed in appendix 2'),
            ('Project size', '%.2f terabytes' % self.project_size_in_terabytes()),
            ('Laboratory protocol', library_workflow),
            ('Submitted species', ', '.join(list(species_submitted))),
            ('Genome version', ', '.join(list(genome_versions)))
        )
        return project_info

    def get_list_of_sample_fields(self, samples, field, subfields=None):
        if subfields:
            sample_fields = [s.get(field, {}) for s in samples if s.get(field)]
            for f in subfields:
                sample_fields = [s.get(f, {}) for s in sample_fields]
            return [s for s in sample_fields if s]
        sample_fields = [s.get(field) for s in samples if s.get(field)]
        return sample_fields

    def gather_project_data(self):
        samples = self.samples_for_project_restapi
        # FIXME: Add support for dor (.) notation
        project_sample_data = {
            'clean_yield':               {'key': 'clean_yield_in_gb', 'subfields': None},
            'coverage_per_sample':       {'key': 'coverage', 'subfields': ['mean']},
            'pc_duplicate_reads':        {'key': 'pc_duplicate_reads', 'subfields': None},
            'evenness':                  {'key': 'evenness', 'subfields': None},
            'freemix':                   {'key': 'sample_contamination', 'subfields': 'freemix'},
            'pc_mapped_reads':           {'key': 'pc_mapped_reads','subfields': None},
            'clean_pc_q30':              {'key': 'clean_pc_q30','subfields': None},
            'mean_bases_covered_at_15X': {'key': 'coverage', 'subfields': ['bases_at_coverage', 'bases_at_15X']}
        }

        for field in project_sample_data:
            project_sample_data[field]['values'] = self.get_list_of_sample_fields(samples,
                                                                                  project_sample_data[field]['key'],
                                                                                  subfields=project_sample_data[field]['subfields'])
        return project_sample_data

    @staticmethod
    def min_mean_max(list_values):
        if list_values:
            return 'min: %.1f, avg: %.1f, max: %.1f' % (
                min(list_values),
                ProjectReport.calculate_mean(list_values),
                max(list_values)
            )
        else:
            return 'min: 0, mean: 0, max: 0'

    def calculate_project_statistsics(self):
        p = self.gather_project_data()
        project_stats = OrderedDict()
        project_stats['Yield per sample (Gb)'] = self.min_mean_max(p['clean_yield']['values'])
        project_stats['% Q30'] = self.min_mean_max(p['clean_pc_q30']['values'])
        project_stats['Coverage per sample'] = self.min_mean_max(p['coverage_per_sample']['values'])
        project_stats['% Reads mapped'] = self.min_mean_max(p['pc_mapped_reads']['values'])
        project_stats['% Duplicate reads'] = self.min_mean_max(p['pc_duplicate_reads']['values'])
        return project_stats

    def get_sample_info(self):
        for sample in set(self.sample_name_delivered):
            sample_source = path.join(self.project_source, sample)
            if self.get_species_from_sample(sample) == 'Human':
                program_csv = find_file(sample_source, 'programs.txt')
                self.update_from_program_csv(program_csv)
                summary_yaml = find_file(sample_source, 'project-summary.yaml')
                if summary_yaml:
                    self.update_from_project_summary_yaml(summary_yaml)
            else:
                program_yaml = find_file(sample_source, 'program_versions.yaml')
                self.update_from_program_version_yaml(program_yaml)

            if not 'genome_version' in self.params:
                self.params['genome_version'] = self.get_genome_version(sample)

            if self.params['genome_version'] == 'hg38':
                self.params['genome_version'] = 'GRCh38 (with alt, decoy and HLA sequences)'

        get_project_stats = self.calculate_project_statistsics()
        project_stats = []
        for stat in get_project_stats:
            if get_project_stats[stat]:
                project_stats.append((stat, get_project_stats[stat]))
        return project_stats

    def get_sample_yield_coverage_metrics(self):
        req_to_metrics = {}
        for sample in self.samples_for_project_restapi:

            req = (self.get_required_yield(sample.get('sample_id')), self.get_quoted_coverage(sample.get('sample_id')))
            if not req in req_to_metrics:
                req_to_metrics[req] = {'samples': [], 'clean_yield': [], 'coverage': []}
            all_yield_metrics = [sample.get('sample_id'),
                                 sample.get('clean_yield_in_gb'),
                                 sample.get('coverage').get('mean')]
            if not None in all_yield_metrics:
                req_to_metrics[req]['samples'].append(all_yield_metrics[0])
                req_to_metrics[req]['clean_yield'].append(all_yield_metrics[1])
                req_to_metrics[req]['coverage'].append(all_yield_metrics[2])
        return req_to_metrics

    def get_sample_yield_metrics(self):
        yield_metrics = {'samples': [], 'clean_yield': [], 'clean_yield_Q30': []}
        for sample in self.samples_for_project_restapi:

            all_yield_metrics = [sample.get('sample_id'),
                                 sample.get('clean_yield_in_gb'),
                                 sample.get('clean_yield_q30')]
            if not None in all_yield_metrics:
                yield_metrics['samples'].append(all_yield_metrics[0])
                yield_metrics['clean_yield'].append(all_yield_metrics[1])
                yield_metrics['clean_yield_Q30'].append(all_yield_metrics[2])
        return yield_metrics

    def get_pc_statistics(self):
        pc_statistics = {'pc_duplicate_reads': [], 'pc_properly_mapped_reads': [], 'pc_pass_filter': [], 'samples': []}
        for sample in self.samples_for_project_restapi:
            all_pc_statistics = [sample.get('pc_duplicate_reads'),
                                 sample.get('pc_properly_mapped_reads'),
                                 sample.get('pc_pass_filter'),
                                 sample.get('sample_id')]
            if not None in all_pc_statistics:
                pc_statistics['pc_duplicate_reads'].append(all_pc_statistics[0])
                pc_statistics['pc_properly_mapped_reads'].append(all_pc_statistics[1])
                pc_statistics['pc_pass_filter'].append(all_pc_statistics[2])
                pc_statistics['samples'].append(all_pc_statistics[3])
        return pc_statistics

    def yield_vs_coverage_plot(self):
        req_to_metrics = self.get_sample_yield_coverage_metrics()
        list_plots = []
        for req in req_to_metrics:
            df = pd.DataFrame(req_to_metrics[req])
            req_yield, req_cov = req
            max_x = max(df['clean_yield']) + .1 * max(df['clean_yield'])
            max_y = max(df['coverage']) + .1 * max(df['coverage'])
            min_x = min(df['clean_yield']) - .1 * max(df['clean_yield'])
            min_y = min(df['coverage']) - .1 * max(df['coverage'])

            min_x = min((min_x, req_yield - .1 * req_yield))
            min_y = min((min_y, req_cov - .1 * req_cov))

            plt.figure(figsize=(10, 5))
            df.plot(kind='scatter', x='clean_yield', y='coverage')

            plt.xlim(min_x, max_x)
            plt.ylim(min_y, max_y)
            plt.xlabel('Delivered yield (Gb)')
            plt.ylabel('Covereage (X)')

            xrange1 = [(0, req_yield)]
            xrange2 = [(req_yield, max_x)]
            yrange1 = (0, req_cov)
            yrange2 = (req_cov, max_y)

            c1 = mpcollections.BrokenBarHCollection(xrange1, yrange1, facecolor='red', alpha=0.2)
            c2 = mpcollections.BrokenBarHCollection(xrange1, yrange2, facecolor='yellow', alpha=0.2)
            c3 = mpcollections.BrokenBarHCollection(xrange2, yrange1, facecolor='yellow', alpha=0.2)
            c4 = mpcollections.BrokenBarHCollection(xrange2, yrange2, facecolor='green', alpha=0.2)

            ax = plt.gca()
            ax.add_collection(c1)
            ax.add_collection(c2)
            ax.add_collection(c3)
            ax.add_collection(c4)

            plot_outfile = path.join(self.working_dir, 'yield%s_cov%s_plot.png' %(req_yield, req_cov))
            plt.savefig(plot_outfile, bbox_inches='tight', pad_inches=0.2)
            list_plots.append({
                'nb_sample': len(df),
                'req_yield': req_yield,
                'req_cov': req_cov,
                'file': 'file://' + os.path.abspath(plot_outfile)
            })
        self.params['yield_cov_chart'] = list_plots

    def yield_plot(self, sample_labels=False):
        yield_plot_outfile = path.join(self.working_dir, 'yield_plot.png')
        sample_yields = self.get_sample_yield_metrics()
        df = pd.DataFrame(sample_yields)
        indices = np.arange(len(df))
        plt.figure(figsize=(10, 5))
        if sample_labels:
            plt.xticks([i for i in range(len(df))], list((df['samples'])), rotation=-80)
        else:
            plt.xticks([])
        plt.xlim([-1, max(indices) + 1])
        plt.bar(indices, df['clean_yield'], width=0.8, align='center', color='gainsboro')
        plt.bar(indices, df['clean_yield_Q30'], width=0.2, align='center', color='lightskyblue')
        plt.ylabel('Gigabases')
        blue_patch = mpatches.Patch(color='gainsboro', label='Yield (Gb)')
        green_patch = mpatches.Patch(color='lightskyblue', label='Yield Q30 (Gb)')
        lgd = plt.legend(handles=[blue_patch, green_patch], loc='upper center', bbox_to_anchor=(0.5, 1.25))
        plt.savefig(yield_plot_outfile, bbox_extra_artists=(lgd,), bbox_inches='tight', pad_inches=0.2)
        yield_plot_outfile = 'file://' + os.path.abspath(yield_plot_outfile)
        self.params['yield_chart'] = yield_plot_outfile

    def qc_plot(self, sample_labels=False):
        qc_plot_outfile = path.join(self.working_dir, 'qc_plot.png')
        pc_statistics = self.get_pc_statistics()
        df = pd.DataFrame(pc_statistics)
        indices = np.arange(len(df))
        plt.figure(figsize=(10, 5))
        if sample_labels:
            plt.xticks([i for i in range(len(df))], list((df['samples'])), rotation=-80)
        else:
            plt.xticks([])
        plt.xlim([-1, max(indices) + 1])
        plt.bar(indices, df['pc_mapped_reads'], width=1, align='center', color='gainsboro')
        plt.bar(indices, df['pc_duplicate_reads'], width=0.4, align='center', color='mediumaquamarine')
        blue_patch = mpatches.Patch(color='gainsboro', label='% Paired Reads Aligned to Reference Genome')
        green_patch = mpatches.Patch(color='mediumaquamarine', label='% Duplicate Reads')
        lgd = plt.legend(handles=[blue_patch, green_patch], loc='upper center', bbox_to_anchor=(0.5, 1.25))
        plt.ylabel('% of Reads')
        plt.savefig(qc_plot_outfile, bbox_extra_artists=(lgd,), bbox_inches='tight', pad_inches=0.2)
        qc_plot_outfile = 'file://' + os.path.abspath(qc_plot_outfile)
        self.params['mapping_duplicates_chart'] = qc_plot_outfile

    def kits_and_equipment(self):
        table_content = {'headings': ['Process', 'Critical equipment', 'Kits'],
                         'rows': [
                        ('Sample QC', 'Fragment analyzer, Hamilton robot', 'Kit 1, Kit 2, Kit 3, Kit 4'),
                        ('Library prep', 'Hamilton Star, '
                                         'Covaris LE220, '
                                         'Gemini Spectramax XP, '
                                         'Hybex incubators, '
                                         'BioRad C1000/S1000 thermal cycler', 'Kit 1, Kit 2, Kit 3, Kit 4'),
                        ('Library QC', 'Caliper GX Touch, Roche Lightcycler', 'Kit 1, Kit 2, Kit 3, Kit 4'),
                        ('Sequencing', 'cBot2, HiSeqX', 'Kit 1, Kit 2, Kit 3, Kit 4')
                        ]}
        return table_content

    def duplicate_marking(self):
        if 'biobambam_sortmapdup_version' in self.params:
            return ('biobambam_sortmapdup', self.params['biobambam_sortmapdup_version'])
        if 'samblaster_version' in self.params:
            return ('samblaster', self.params['samblaster_version'])

    def method_fields(self):
        fields = {'sample_qc': {'title': 'Sample QC',
                                'headings': ['Method', 'QC', 'Critical equipment', 'Pass criteria'],
                                'rows': [('Sample picogreen', 'gDNA quantified against Lambda DNA standards', 'Hamilton robot', '> 1000ng'),
                                         ('Fragment analyzer QC', 'Quality of gDNA determined', 'Fragment analyzer', 'Quality score > 5'),
                                         ('gDNA QC Review Process', 'N/A', 'N/A', 'N/A')]},
                  'library_prep': {'title': 'Library preparation',
                                   'headings': ['Method', 'Purpose', 'Critical equipment'],
                                   'rows': [('Sequencing plate preparation', 'Samples normalised to fall within 5-40ng/ul', 'Hamilton robot'),
                                            ('Nano DNA', 'Libraries prepared using Illumina SeqLab %s' % (self.params['library_workflow']), 'Hamilton, Covaris LE220, Gemini Spectramax XP, Hybex incubators, BioRad C1000/S1000 thermal cycler')]},
                  'library_qc': {'title': 'Library QC',
                                 'headings': ['Method', 'QC', 'Critical equipment', 'Pass criteria'],
                                 'rows': [('Library QC as part of Nano DNA', 'Insert size evaluated', 'Caliper GX Touch', 'Fragment sizes fall between 530bp and 730bp'),
                                          ('Library QC as part of Nano DNA', 'Library concentration calculated', 'Roche Lightcycler', 'Concentration between 5.5nM and 40nM')]},
                  'sequencing': {'title': 'Sequencing',
                                 'headings': ['Method', 'Steps', 'Critical equipment'],
                                 'rows': [('Clustering and sequencing of libraries as part of %s' % (self.params['library_workflow']), 'Clustering', 'cBot2'),
                                          ('Clustering and Sequencing of libraries as part of %s' % (self.params['library_workflow']), 'Sequencing', 'HiSeqX')]},
                  'bioinformatics': {'title': 'Bioinformatics analysis',
                                     'headings': ['Method', 'Software', 'Version'],
                                     'rows': [('Demultiplexing', 'bcl2fastq', self.params['bcl2fastq_version']),
                                      ('Alignment', 'bwa mem', self.params['bwa_version']),
                                      ('Duplicates marking',) + self.duplicate_marking(),
                                      ('Indel realignment', 'GATK IndelRealigner', self.params['gatk_version']),
                                      ('Base recalibration', 'GATK BaseRecalibrator', self.params['gatk_version']),
                                      ('Genotype likelihood calculation', 'GATK HaplotypeCaller', self.params['gatk_version'])]}
                  }
        return fields

    def get_html_template(self):
        template = {'template_base': 'report_base.html',
                    'glossary': [],
                    'charts_template': ['yield_cov_chart']}

        species = self.get_species(self.sample_name_delivered)
        analysis_type = self.get_analysis_type(self.sample_name_delivered)
        library_workflow = self.get_library_workflow(self.sample_name_delivered)
        if len(species) == 1 and species.pop() == 'Human':
            bioinfo_template = ['bioinformatics_analysis_bcbio']
            formats_template = ['fastq', 'bam', 'vcf']
        elif analysis_type and analysis_type in ['Variant Calling', 'Variant Calling gatk']:
            bioinfo_template = ['bioinformatics_analysis']
            formats_template = ['fastq', 'bam', 'vcf']
        else:
            bioinfo_template = ['bioinformatics_qc']
            formats_template = ['fastq']
        template['bioinformatics_template'] = bioinfo_template
        template['formats_template'] = formats_template

        self.params['library_workflow'] = library_workflow
        workflow_alias = self.workflow_alias.get(library_workflow)
        if not workflow_alias:
            raise EGCGError('No workflow found for project %s' % self.project_name)
        template['laboratory_template'] = ['sample_qc', 'sample_qc_table',  workflow_alias, 'library_prep_table', 'library_qc', 'library_qc_table','sequencing', 'sequencing_table']
        return template

    def generate_report(self, output_format):
        project_file = path.join(self.project_delivery, 'project_%s_report.%s' % (self.project_name, output_format))
        if not HTML:
            raise ImportError('Could not import WeasyPrint - PDF output not available')
        else:
            report_render, pages, full_html = self.get_html_content()
        if output_format == 'html':
            open(project_file, 'w').write(full_html)
        elif HTML:
            report_render.copy(pages).write_pdf(project_file)

    def get_csv_data(self):
        header = ['Internal ID',
                    'External ID',
                    'DNA QC (>1000 ng)',
                    'Date received',
                    'Species',
                    'Workflow',
                    'Yield quoted (Gb)',
                    'Yield provided (Gb)',
                    '% Q30 > 75%',
                    'Quoted coverage',
                    'Provided coverage'
                    ]


        rows = []
        for sample in self.samples_for_project_restapi:
            internal_sample_name = self.get_fluidx_barcode(sample.get('sample_id')) or sample.get('sample_id')
            row = [
                internal_sample_name,
                sample.get('user_sample_id', 'None'),
                self.get_sample_total_dna(sample.get('sample_id')),
                self.parse_date(self.sample_status(sample.get('sample_id')).get('started_date')),
                sample.get('species_name'),
                self.get_library_workflow_from_sample(sample.get('sample_id')),
                self.get_required_yield(sample.get('sample_id')),
                round(sample.get('clean_yield_in_gb', 'None'), 2),
                round(sample.get('clean_pc_q30', 'None'), 2),
                self.get_quoted_coverage(sample.get('sample_id')),
                sample.get('coverage', {}).get('mean', 'None')
            ]

            rows.append(row)
        return (header, rows)

    def write_csv_file(self):
        csv_file = path.join(self.project_delivery, 'project_data.csv')
        headers, rows = self.get_csv_data()
        with open(csv_file, 'w') as outfile:
            writer = csv.writer(outfile, delimiter='\t')
            writer.writerow(headers)
            for row in rows:
                writer.writerow(row)
        return csv_file

    def get_html_content(self):
        sample_labels = False
        if not self.get_all_sample_names():
            raise EGCGError('No samples found for project %s ' % (self.project_name))
        if len(self.get_all_sample_names()) < 35:
            sample_labels = True
        #self.yield_plot(sample_labels=sample_labels)
        #self.qc_plot(sample_labels=sample_labels)
        self.yield_vs_coverage_plot()

        #self.params['csv_path'] = self.write_csv_file()
        self.params['csv_path'] = 'summary_metrics.csv'
        template_dir = path.join(path.dirname(path.abspath(__file__)), 'templates')
        env = Environment(loader=FileSystemLoader(template_dir))
        project_templates = self.get_html_template()
        template1 = env.get_template(project_templates.get('template_base'))
        template2 = env.get_template('csv_base.html')
        report = template1.render(
            project_info=self.get_project_info(),
            project_stats=self.get_sample_info(),
            project_templates=project_templates,
            params=self.params
        )

        csv_table_headers, csv_table_rows  = self.get_csv_data()
        appendices = template2.render(
            report_csv_headers=csv_table_headers,
            report_csv_rows=csv_table_rows,
            csv_path=self.params['csv_path'],
            project_id=self.params['project_name'],
            method_fields=self.kits_and_equipment()
        )
        combined_report_html = (report + appendices)
        report_html = HTML(string=report)
        appendices_html = HTML(string=appendices)
        report_render = report_html.render(font_config=self.font_config)
        appendices_render = appendices_html.render(font_config=self.font_config)
        pages = []
        for doc in report_render, appendices_render:
            for p in doc.pages:
                pages.append(p)
        return report_render, pages, combined_report_html

    @classmethod
    def get_folder_size(cls, folder):
        total_size = path.getsize(folder)
        for item in listdir(folder):
            itempath = path.join(folder, item)
            if path.isfile(itempath):
                total_size += path.getsize(itempath)
            elif path.isdir(itempath):
                total_size += cls.get_folder_size(itempath)
        return total_size
