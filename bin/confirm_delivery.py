import argparse
import csv
import glob
import logging
import os
import sys
from collections import defaultdict
from os.path import join, dirname, abspath, relpath
import datetime
import itertools

from cached_property import cached_property
from egcg_core import clarity

from egcg_core.app_logging import AppLogger, logging_default as log_cfg
from egcg_core.config import cfg
from egcg_core.rest_communication import get_document, patch_entry
from pyclarity_lims.entities import Step, Queue

sys.path.append(dirname(dirname(abspath(__file__))))
from config import load_config

file_extensions_to_check = [
    'fastq.gz',
    'bam',
    'g.vcf.gz'
]

lims_workflow_name = 'PostSeqLab EG 1.0 WF'
lims_protocol_name = 'Data Release EG 2.0 PR'
lims_stage_name = 'Download Confirmation EG 1.0 ST'


def parse_aspera_reports(report_csv):
    all_files = []
    with open(report_csv) as open_report:
        # ignore what is before the second blank lines
        blank_line = 0
        for l in open_report:
            if not l.strip():
                blank_line += 1
            if blank_line == 2:
                break

        dict_reader = csv.DictReader(open_report)
        for file_dict in dict_reader:
            if file_dict['level'] == '1':
                all_files.append((
                    '/'.join(file_dict['file_path'].split('/')[3:]),
                    file_dict['ssh_user'],
                    datetime.datetime.strptime(file_dict['stopped_at'], '%Y/%m/%d %H:%M:%S'),  # 2016/09/08 16:30:27
                    file_dict['bytes_transferred']
                ))
    return all_files


def check_end_file_name(f):
    for ext in file_extensions_to_check:
        if f.endswith(ext):
            return True
    return False


def list_files_delivered(path):
    all_files = {}
    for root, dirs, files in os.walk(path):
        for f in files:
            if check_end_file_name(f):
                all_files[join(root, f)] = os.stat(join(root, f))
    return all_files


class DeliveredSample(AppLogger):

    def __init__(self, sample_id):
        self.sample_id = sample_id
        # resolve fluidX sample name
        if self.sample_id.startswith('FD'):
            # might be FluidX tube barcdeo
            lims = clarity.connection()
            arts = lims.get_artifacts(type='Analyte', udf={'2D Barcode': self.sample_id})
            if arts and len(arts) == 1:
                self.sample_id = arts[0].samples[0].name
        self.delivery_dir = abspath(cfg.query('delivery', 'dest'))
        self.list_file_downloaded = []

    @cached_property
    def data(self):
        d = get_document('samples', where={'sample_id': self.sample_id})
        if not d:
            self.warning('No data found for sample %s', self.sample_id)
            return {}
        return d

    @property
    def sample_folders(self):
        return glob.glob(join(self.delivery_dir, self.data.get('project_id'), '*', self.sample_id)) or []

    def _format_list_files(self, list_file):
        list_file_to_upload = []
        for f in list_file:
            if check_end_file_name(f):
                with open(f + '.md5') as open_file:
                    md5 = open_file.readline().strip().split()[0]
            rel_path = relpath(f, start=self.delivery_dir)
            list_file_to_upload.append({'file_path': rel_path, 'md5': md5, 'size': os.stat(f).st_size})
        return list_file_to_upload

    def upload_list_file_delivered(self, list_file):
        list_file_to_upload = self._format_list_files(list_file)
        patch_entry(
            'samples',
            payload={'files_delivered': list_file_to_upload},
            id_field='sample_id',
            element_id=self.sample_id,
            update_lists=['files_delivered']
        )
        return list_file_to_upload

    @property
    def list_file_delivered(self):
        list_file = self.data.get('files_delivered')
        if not list_file:
            list_file = []
            for sample_folder in self.sample_folders:
                list_file.extend(list_files_delivered(sample_folder))
            list_file = self.upload_list_file_delivered(list_file)
            # delete the cached data
            del self.__dict__['data']
        return list_file

    @property
    def list_file_already_downloaded(self):
        return self.data.get('files_downloaded', [])

    def add_file_downloaded(self, file_name, user, date_downloaded, file_size):
        self.list_file_downloaded.append({
            'file_path': file_name,
            'user': user,
            'date': date_downloaded.strftime('%d_%m_%Y_%H:%M:%S'),
            'size': file_size}
        )

    def update_list_file_downloaded(self):

        # Make sure you're only adding files that were not there before
        new_list_file_downloaded = list(
            itertools.filterfalse(lambda x: x in self.list_file_already_downloaded, self.list_file_downloaded)
        )
        if new_list_file_downloaded:
            patch_entry(
                'samples',
                payload={'files_downloaded': list(new_list_file_downloaded)},
                id_field='sample_id',
                element_id=self.sample_id,
                update_lists=['files_downloaded']
            )

    def files_missing(self):
        file_downloaded = set([f['file_path'] for f in self.list_file_downloaded])
        file_downloaded.update([f['file_path'] for f in self.list_file_already_downloaded])
        return [f['file_path'] for f in self.list_file_delivered if f['file_path'] not in file_downloaded]

    def is_download_complete(self):
        return len(self.files_missing()) == 0


class ConfirmDelivery(AppLogger):
    def __init__(self, aspera_report_csv_files=None):
        self.delivery_dir = cfg.query('delivery', 'dest')
        self.samples_delivered = defaultdict(DeliveredSample)
        self.confirmed_samples = list()
        if aspera_report_csv_files:
            for aspera_report_csv_file in aspera_report_csv_files:
                self.read_aspera_report(aspera_report_csv_file)
            self.update_samples()

    def get_sample_delivered(self, sample_id):
        if not sample_id in self.samples_delivered:
            s = DeliveredSample(sample_id)
            # Check that the sample exists before caching it
            if s.data:
                self.samples_delivered[sample_id] = s
        return self.samples_delivered.get(sample_id)

    def read_aspera_report(self, aspera_report_csv_file):
        confirmed_files = parse_aspera_reports(aspera_report_csv_file)
        for fname, user, date, size in confirmed_files:
            if len(fname.split('/')) > 2 and self.get_sample_delivered(fname.split('/')[2]):
                self.get_sample_delivered(fname.split('/')[2]).add_file_downloaded(
                    file_name=fname,
                    user=user,
                    date_downloaded=date,
                    file_size=size
                )
            else:
                self.warning('Cannot detect sample name from %s', fname)

    def update_samples(self):
        for sample in  self.samples_delivered.values():
            sample.update_list_file_downloaded()

    def test_sample(self, sample_id):
        files_missing = self.get_sample_delivered(sample_id).files_missing()
        if files_missing:
            self.info('Sample %s has not been fully downloaded: %s files missing', sample_id, len(files_missing))
            for file_missing in files_missing:
                self.info('    - '+ file_missing)
            return False
        else:
            self.confirmed_samples.append(sample_id)
            return True

    def confirm_download_in_lims(self):
        if len(self.confirmed_samples):
            lims = clarity.connection()
            stage = clarity.get_workflow_stage(workflow_name=lims_workflow_name, stage_name=lims_stage_name)

            queue = Queue(lims, id=stage.step.id)
            samples_name_queued = set()
            artifacts_to_confirm = []

            # find all samples that are queued and confirmed
            artifacts = queue.artifacts
            for a in artifacts:
                assert len(a.samples) == 1
                if a.samples[0].name in self.confirmed_samples:
                    artifacts_to_confirm.append(a)
                samples_name_queued.add(a.samples[0].name)

            # find all samples that are confirmed but not queued
            confirmed_but_not_queued = set(self.confirmed_samples).difference(samples_name_queued)
            if confirmed_but_not_queued:
                samples_to_confirm = clarity.get_list_of_samples(sample_names=list(confirmed_but_not_queued))
                artifacts = [s.artifact for s in samples_to_confirm]
                #Queue the artifacts there were not already there
                lims.route_artifacts(artifact_list=artifacts, stage_uri=stage.uri)
                artifacts_to_confirm.extend(artifacts)

            # Create a new step from that queued artifact
            s = Step.create(lims, protocol_step=stage.step, inputs=artifacts_to_confirm)

            # Move from "Record detail" window to the "Next Step"
            s.advance()

            # Set the next step to complete
            for action in s.actions.next_actions:
                action['action'] = 'complete'
            s.actions.put()

            # Complete the step
            s.advance()

            self.confirmed_samples = list()


    def test_all_queued_samples(self):
        lims = clarity.connection()
        stage = clarity.get_workflow_stage(workflow_name=lims_workflow_name, stage_name=lims_stage_name)
        # Queue have the same id as the ProtocolStep
        queue = Queue(lims, id=stage.step.id)
        samples = set()
        artifacts = queue.artifacts
        for a in artifacts:
            samples.update(a.samples)
        for sample in samples:
            self.test_sample(sample.name)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--csv_files', type=str, nargs='+')
    group = p.add_mutually_exclusive_group()
    group.add_argument('--samples', type=str, nargs='+')
    group.add_argument('--queued_samples', action='store_true', default=False,
                   help='Test samples queued to the Data Download confirmation step.')
    p.add_argument('--confirm_in_lims', action='store_true', default=False,
                   help='Confirm all successfully tested samples.')

    p.add_argument('--debug', action='store_true')
    args = p.parse_args(argv)

    load_config()

    log_cfg.add_handler(logging.StreamHandler(stream=sys.stdout), level=logging.INFO)
    if args.debug:
        log_cfg.set_log_level(logging.DEBUG)

    cfg.merge(cfg['sample'])

    cd = ConfirmDelivery(aspera_report_csv_files=args.csv_files)
    if args.samples:
        for sample in args.samples:
            cd.test_sample(sample)
    elif args.queued_samples:
        cd.test_all_queued_samples()
    if args.confirm_in_lims:
        cd.confirm_download_in_lims()


if __name__ == '__main__':
    main()
