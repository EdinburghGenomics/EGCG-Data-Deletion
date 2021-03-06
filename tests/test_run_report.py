from bin import report_runs
from pytest import raises
from unittest.mock import patch, call

from bin.report_runs import remove_duplicate_base_on_flowcell_id


@patch('egcg_core.rest_communication.get_documents')
def test_run_status_data(mocked_get_docs):
    report_runs.cache['run_status_data'] = {}
    fake_rest_data = [{'run_id': 1, 'some': 'data'}, {'run_id': 2, 'some': 'data'}]
    mocked_get_docs.return_value = fake_rest_data

    obs = report_runs.run_status_data(2)
    assert obs == fake_rest_data[1]
    assert report_runs.cache['run_status_data'] == {1: fake_rest_data[0], 2: fake_rest_data[1]}
    mocked_get_docs.assert_called_with('lims/status/run_status')
    assert mocked_get_docs.call_count == 1

    obs = report_runs.run_status_data(1)
    assert obs == fake_rest_data[0]
    assert mocked_get_docs.call_count == 1  # not called again


@patch('egcg_core.rest_communication.get_documents')
def test_run_element_data(mocked_get_docs):
    report_runs.cache['run_elements_data'] = {}

    mocked_get_docs.return_value = 'some data'
    obs = report_runs.run_elements_data('a_run')
    assert obs == 'some data'
    assert report_runs.cache['run_elements_data'] == {'a_run': 'some data'}
    mocked_get_docs.assert_called_with('run_elements', where={'run_id': 'a_run'})
    assert mocked_get_docs.call_count == 1

    obs = report_runs.run_elements_data('a_run')
    assert obs == 'some data'
    assert mocked_get_docs.call_count == 1  # not called again


@patch('egcg_core.rest_communication.get_document')
def test_run_data(mocked_get_docs):
    report_runs.cache['run_data'] = {}

    mocked_get_docs.return_value = 'some data'
    obs = report_runs.run_data('a_run')
    assert obs == 'some data'
    assert report_runs.cache['run_data'] == {'a_run': 'some data'}
    mocked_get_docs.assert_called_with('runs', where={'run_id': 'a_run'})
    assert mocked_get_docs.call_count == 1

    obs = report_runs.run_data('a_run')
    assert obs == 'some data'
    assert mocked_get_docs.call_count == 1  # not called again


@patch('egcg_core.rest_communication.get_document')
def test_sample_data(mocked_get_docs):
    report_runs.cache['sample_data'] = {}

    mocked_get_docs.return_value = 'some data'
    obs = report_runs.sample_data('a_sample')
    assert obs == 'some data'
    assert report_runs.cache['sample_data'] == {'a_sample': 'some data'}
    mocked_get_docs.assert_called_with('samples', where={'sample_id': 'a_sample'})
    assert mocked_get_docs.call_count == 1

    obs = report_runs.sample_data('a_sample')
    assert obs == 'some data'
    assert mocked_get_docs.call_count == 1  # not called again


@patch('bin.report_runs.logger')
def test_get_run_success(mocked_logger):
    report_runs.cache['run_elements_data'] = {
        'a_run': [
            {'lane': 1, 'reviewed': 'pass'},
            {'lane': 2, 'reviewed': 'fail', 'review_comments': 'Failed due to things'},
            {'lane': 3, 'reviewed': 'fail', 'review_comments': 'Failed due to thungs'},
        ]
    }

    assert report_runs.get_run_success('a_run') == {
        'name': 'a_run',
        'failed_lanes': 2,
        'details': ['lane 2: things', 'lane 3: thungs']
    }
    assert mocked_logger.mock_calls == [
        call.info('a_run: 2 lanes failed:'),
        call.info('lane 2: things'),
        call.info('lane 3: thungs')
    ]
    report_runs.cache['run_elements_data']['a_run'].append(
        {'lane': 1, 'reviewed': 'fail', 'review_comments': 'this will break stuff'}
    )

    with raises(ValueError) as e:
        report_runs.get_run_success('a_run')

    assert str(e.value) == 'More than one review status for lane 1 in run a_run'


@patch('bin.report_runs.send_html_email')
@patch('bin.report_runs.get_run_success', return_value={'name': 'run_id_id_lane1', 'failed_lanes': 0, 'details': []})
@patch('bin.report_runs.today', return_value='today')
def test_report_runs(mocked_today, mocked_run_success, mocked_email):
    report_runs.cfg.content = {'run_report': {'email_notification': {}}}
    report_runs.cache['run_status_data'] = {
        'run_id_id_lane1': {
            'run_status': 'RunCompleted',
            'sample_ids': ['passing', 'no_data', 'excluded_no_data', 'poor_yield', 'poor_coverage', 'poor_yield_and_coverage']
        },
        'errored_id_id_lane2': {
            'run_status': 'RunErrored',
            'sample_ids': ['passing', 'no_data', 'excluded_no_data', 'poor_yield', 'poor_coverage', 'poor_yield_and_coverage']
        },
        'excluded_id_id_lane3': {
            'run_status': 'RunErrored',
            'sample_ids': ['excluded_no_data']
        }
    }

    report_runs.cache['run_data'] = {
        'run_id_id_lane1': {
            'aggregated': {
                'most_recent_proc': {
                    'status': 'finished'
                }
            }
        },
        'errored_id_id_lane2': {
            'aggregated': {
                'most_recent_proc': {
                    'status': 'finished'
                }
            }
        },
        'excluded_id_id_lane3': {
            'aggregated': {
                'most_recent_proc': {
                    'status': 'processing'
                }
            }
        }
    }

    report_runs.cache['sample_data'] = {
        'passing': {'aggregated': {'clean_pc_q30': 75, 'clean_yield_in_gb': 2, 'from_run_elements': {'mean_coverage': 4}}},
        'no_data': {'aggregated': {}},
        'excluded_no_data': {'aggregated': {}},
        'poor_yield': {'aggregated': {'clean_pc_q30': 75, 'clean_yield_in_gb': 1, 'from_run_elements': {'mean_coverage': 4}}},
        'poor_coverage': {'aggregated': {'clean_pc_q30': 75, 'clean_yield_in_gb': 2, 'from_run_elements': {'mean_coverage': 3}}},
        'poor_yield_and_coverage': {'aggregated': {'clean_pc_q30': 75, 'clean_yield_in_gb': 1, 'from_run_elements': {'mean_coverage': 3}}},
    }

    for s in report_runs.cache['sample_data'].values():
        s['required_yield'] = 2000000000
        s['required_coverage'] = 4
        s['run_elements'] = ['run_id_id_lane1_barcode', 'errored_id_id_lane2_barcode']

    # Adding excluded_id_id_lane3 run element only to the excluded_no_data sample
    report_runs.cache['sample_data']['excluded_no_data']['run_elements'].append('excluded_id_id_lane3')

    report_runs.report_runs(['run_id_id_lane1', 'errored_id_id_lane2', 'excluded_id_id_lane3'])
    mocked_email.assert_any_call(
        subject='Run report today',
        email_template=report_runs.email_template_report,
        runs=[
            {'name': 'errored_id_id_lane2', 'failed_lanes': 8, 'details': ['RunErrored']},
            {'name': 'excluded_id_id_lane3', 'failed_lanes': 8, 'details': ['RunErrored']},
            {'name': 'run_id_id_lane1', 'failed_lanes': 0, 'details': []},
        ]
    )

    exp_failing_samples = [
        {'id': 'no_data', 'reason': 'No data'},
        {'id': 'poor_yield_and_coverage', 'reason': 'Not enough data: yield (1.0 < 2) and coverage (3 < 4)'}
    ]

    mocked_email.assert_any_call(
        subject='Sequencing repeats today',
        email_template=report_runs.email_template_repeats,
        runs=[

            {'name': 'errored_id_id_lane2', 'repeat_count': 2, 'repeats': exp_failing_samples},
            {'name': 'excluded_id_id_lane3', 'repeat_count': 0, 'repeats': []},
            {'name': 'run_id_id_lane1', 'repeat_count': 2, 'repeats': exp_failing_samples}
        ]
    )


def test_remove_duplicate_base_on_flowcell_id():
    list_runs = ['190423_E0304_238_BFCC68CCXY', '190423_E0304_239_BACC67CCXX', '190424_E0304_240_AACC67CCXX']
    expected_list_runs = ['190423_E0304_238_BFCC68CCXY', '190424_E0304_240_AACC67CCXX']
    assert remove_duplicate_base_on_flowcell_id(list_runs) == expected_list_runs
