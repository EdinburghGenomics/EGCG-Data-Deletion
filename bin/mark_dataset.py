import sys
import logging
import argparse
from os.path import dirname, abspath
from egcg_core import rest_communication
from egcg_core.app_logging import logging_default as log_cfg
from egcg_core.config import cfg
sys.path.append(dirname(dirname(abspath(__file__))))
from config import load_config


def main():
    load_config()

    log_cfg.cfg = cfg.content
    log_cfg.set_log_level(logging.DEBUG)
    log_cfg.add_stdout_handler()

    args = _parse_args()
    if args.run:
        end_point = 'run_elements'
        filters = [{'run_id': r} for r in args.run]
    elif args.lane:
        end_point = 'run_elements'
        filters = [{'run_id': '_'.join(l.split('_')[:-1]), 'lane': int(l.split('_')[-1])} for l in args.lane]
    elif args.run_element:
        end_point = 'run_elements'
        filters = [{'run_element_id': r} for r in args.run_element]
    elif args.sample:
        end_point = 'samples'
        filters = [{'sample_id': s} for s in args.sample]
    else:
        return 1

    patch = {}
    if args.useable:
        patch['useable'] = 'yes'
    elif args.notuseable:
        patch['useable'] = 'no'
    elif args.resetuseable:
        patch['useable'] = 'not marked'

    if args.review_pass:
        patch['reviewed'] = 'pass'
    elif args.review_fail:
        patch['reviewed'] = 'fail'
    elif args.review_reset:
        patch['reviewed'] = 'not reviewed'

    for f in filters:
        rest_communication.patch_entries(end_point, payload=patch, where=f)


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument('--debug', action='store_true', help='override pipeline log level to debug')
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--run', nargs='+', default=[], help='Mark provided run with specific annotation')
    group.add_argument('--lane', nargs='+', default=[], help='Mark provided lane with specific annotation')
    group.add_argument('--run_element', nargs='+', default=[], help='Mark provided run element (barcode) with specific annotation')
    group.add_argument('--sample', nargs='+', default=[], help='Mark provided sample with specific annotation')
    group = p.add_mutually_exclusive_group(required=False)
    group.add_argument('--useable', action='store_true', default=False)
    group.add_argument('--notuseable', action='store_true', default=False)
    group.add_argument('--resetuseable', action='store_true', default=False)
    group = p.add_mutually_exclusive_group(required=False)
    group.add_argument('--review_pass', action='store_true', default=False)
    group.add_argument('--review_fail', action='store_true', default=False)
    group.add_argument('--review_reset', action='store_true', default=False)
    return p.parse_args()


if __name__ == '__main__':
    sys.exit(main())
