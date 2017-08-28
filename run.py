
import sys
import re
import argparse
import os
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import select
from sqlalchemy import create_engine

from adsdata import nonbib
from adsdata import metrics
from adsdata import reader
from adsputils import load_config, setup_logging
from adsmsg import NonBibRecord, NonBibRecordList, MetricsRecord, MetricsRecordList
from adsdata.tasks import task_output_results, task_output_metrics

logger = None
config = {}


def load_column_files(config, nonbib_db_engine, nonbib_db_conn, sql_sync):
    """ use psycopg.copy_from to data from column file to postgres
    
    after data has been loaded, join to create a unified row view 
    """
    raw_conn = nonbib_db_engine.raw_connection()
    cur = raw_conn.cursor()
    
    for t in nonbib.NonBib.all_types:
        table_name = sql_sync.schema + '.' + t
        logger.info('processing {}'.format(table_name))
        filename = config['DATA_PATH'] + config[t.upper()]
        if t == 'canonical':
            r = reader.BibcodeFileReader(filename)
        elif t == 'refereed':
            r = reader.RefereedFileReader(filename) 
        else:
            r = reader.StandardFileReader(t, filename)
        if r:
            cur.copy_from(r, table_name)
            raw_conn.commit()

    cur.close()
    raw_conn.close()
    sql_sync.create_joined_rows(nonbib_db_conn)


def nonbib_to_master_pipeline(schema, batch_size=1):
    """send all nonbib data to queue for delivery to master pipeline"""
    nonbib = nonbib.NonBib(schema)
    connection = nonbib.engine.connect()
    s = select([nonbib.table])
    results = connection.execute(s)
    tmp = []
    for current_row in results:
        current_row = dict(current_row)
        rec = NonBibRecord(**current_row)
        tmp.append(rec._data)
        if len(tmp) >= batch_size:
            recs = NonBibRecordList()
            recs.nonbib_records.extend(tmp)
            tmp = []
            logger.debug("Calling 'app.forward_message' with '%s' items", len(recs.nonbib_records))
            task_output_results.delay(recs)

    if len(tmp) > 0:
        recs = NonBibRecordList()
        recs.nonbib_records.extend(tmp)
        logger.debug("Calling 'app.forward_message' with final '%s' items", len(recs.nonbib_records))
        task_output_results.delay(recs)



def nonbib_delta_to_master_pipeline(schema, batch_size=1):
    """send data for changed bibcodes to master pipeline

    the delta table was computed by comparing to sets of nonbib data
    perhaps ingested on succesive days"""
    nonbib = nonbib.NonBib(schema)
    connection = nonbib.engine.connect()
    delta_table = nonbib.get_delta_table()
    s = select([delta_table])
    results = connection.execute(s)
    tmp = []
    for current_delta in results:
        row = nonbib.get_row_view(current_delta['bibcode'])
        rec = NonBibRecord(**dict(row))
        tmp.append(rec._data)
        if len(tmp) >= batch_size:
            recs = NonBibRecordList()
            recs.nonbib_records.extend(tmp)
            tmp = []
            logger.debug("Calling 'app.forward_message' with '%s' items", len(recs.nonbib_records))
            task_output_results.delay(recs)

    if len(tmp) > 0:
        recs = NonBibRecordList()
        recs.nonbib_records.extend(tmp)
        logger.debug("Calling 'app.forward_message' with final '%s' items", len(recs.nonbib_records))
        task_output_results.delay(recs)


def metrics_to_master_pipeline(schema, batch_size=1):
    """send all metrics data to queue for delivery to master pipeline"""
    m = metrics.Metrics(schema)
    connection = m.engine.connect()
    s = select([m.table])
    results = connection.execute(s)
    tmp = []
    for current_row in results:
        current_row = dict(current_row)
        current_row.pop('id')
        logger.debug('Will forward this metrics record: %s', current_row)
        rec = MetricsRecord(**current_row)
        tmp.append(rec._data)
        if len(tmp) >= batch_size:
            recs = MetricsRecordList()
            recs.metrics_records.extend(tmp)
            logger.debug("Calling metrics 'app.forward_message' with '%s' records", len(recs.metrics_records))
            task_output_metrics.delay(recs)
            tmp = []

    if len(tmp) > 0:
        recs = MetricsRecordList()
        recs.metrics_records.extend(tmp)
        logger.debug("Calling metrics 'app.forward_message' with final '%s' records", len(recs.metrics_records))
        task_output_metrics.delay(recs)



def metrics_delta_to_master_pipeline(metrics_schema, nonbib_schema, batch_size=1):
    """send data for changed metrics to master pipeline

    the delta table was computed by comparing to sets of nonbib data
    perhaps ingested on succesive days"""
    m = metrics.Metrics(metrics_schema)
    nonbib = nonbib.NonBib(nonbib_schema)
    connection = nonbib.engine.connect()
    delta_table = nonbib.get_delta_table()
    s = select([delta_table])
    results = connection.execute(s)
    tmp = []
    for current_delta in results:
        row = m.read(current_delta['bibcode'])
        row.pop('id')
        rec = MetricsRecord(**dict(row))
        tmp.append(rec._data)
        if len(recs.metrics_records) >= batch_size:
            recs = MetricsRecordList()
            recs.metrics_records.extend(tmp)
            logger.debug("Calling metrics 'app.forward_message' with '%s' messages", len(recs.metrics_records))
            task_output_metrics.delay(recs)
            tmp = []

    if len(tmp) > 0:
        recs = MetricsRecordList()
        recs.metrics_records.extend(tmp)
        logger.debug("Calling metrics 'app.forward_message' with final '%s'", str(rec))
        task_output_metrics.delay(recs)



def diagnose_nonbib():
    """send hard coded nonbib data the master pipeline

    useful for testing to verify connectivity"""

    test_data = {'bibcode': '2003ASPC..295..361M', 'id': 6473150, 'authors': ["Mcdonald, S","Buddha, S"],
                 'refereed': False, 'simbad_objects': [], 'grants': ['g'], 'citations': [], 'boost': 0.11,
                 'citation_count': 0, 'read_count': 2, 'readers': ['a', 'b'],
                 'downloads': [0,0,0,0,0,0,0,0,0,0,0,1,2,1,0,0,1,0,0,0,1,2],
                 'reference': ['c', 'd'], 
                 'reads': [0,0,0,0,0,0,0,0,1,0,4,2,5,1,0,0,1,0,0,2,4,5]}
    recs = NonBibRecordList()
    rec = NonBibRecord(**test_data)
    recs.nonbib_records.extend([rec._data])
    print 'sending nonbib data for bibocde', test_data['bibcode'], 'to master pipeline'
    print 'using CELERY_BROKER', config['CELERY_BROKER']
    print '  CELERY_DEFAULT_EXCHANGE', config['CELERY_DEFAULT_EXCHANGE']
    print '  CELERY_DEFAULT_EXCHANGE_TYPE', config['CELERY_DEFAULT_EXCHANGE_TYPE']
    print '  OUTPUT_CELERY_BROKER', config['OUTPUT_CELERY_BROKER']
    print '  OUTPUT_TASKNAME', config['OUTPUT_TASKNAME']
    print 'this action did not use ingest database (configured at', config['INGEST_DATABASE'], ')'
    print '  or the metrics database (at', config['METRICS_DATABASE'], ')'
    task_output_results.delay(recs)


def diagnose_metrics():
    """send hard coded metrics data the master pipeline

    useful for testing to verify connectivity"""

    test_data = {'bibcode': '2003ASPC..295..361M', 'refereed':False, 'rn_citations': 0, 
                 'rn_citation_data': [], 
                 'downloads': [0,0,0,0,0,0,0,0,0,0,0,1,2,1,0,0,1,0,0,0,1,2],
                 'reads': [0,0,0,0,0,0,0,0,1,0,4,2,5,1,0,0,1,0,0,2,4,5], 
                 'an_citations': 0, 'refereed_citation_num': 0,
                 'citation_num': 0, 'reference_num': 0, 'citations': [], 'refereed_citations': [], 
                 'author_num': 2, 'an_refereed_citations': 0
                 }
    recs = MetricsRecordList()
    rec = MetricsRecord(**test_data)
    recs.metrics_records.extend([rec._data])
    print 'sending metrics data for bibocde', test_data['bibcode'], 'to master pipeline'
    print 'using CELERY_BROKER', config['CELERY_BROKER']
    print '  CELERY_DEFAULT_EXCHANGE', config['CELERY_DEFAULT_EXCHANGE']
    print '  CELERY_DEFAULT_EXCHANGE_TYPE', config['CELERY_DEFAULT_EXCHANGE_TYPE']
    print '  OUTPUT_CELERY_BROKER', config['OUTPUT_CELERY_BROKER']
    print '  OUTPUT_TASKNAME', config['OUTPUT_TASKNAME']
    print 'this action did not use ingest database (configured at', config['INGEST_DATABASE'], ')'
    print '  or the metrics database (at', config['METRICS_DATABASE'], ')'
    task_output_metrics.delay(recs)



    
def main():
    parser = argparse.ArgumentParser(description='process column files into Postgres')
    parser.add_argument('--fileType', default=None, help='all,downloads,simbad,etc.')
    parser.add_argument('-r', '--rowViewSchemaName', default='nonbib', help='name of the postgres row view schema')
    parser.add_argument('-m', '--metricsSchemaName', default='metrics', help='name of the postgres metrics schema')
    parser.add_argument('-b', '--rowViewBaselineSchemaName', default='nonbibstaging', 
                        help='name of old postgres schema, used to compute delta')
    parser.add_argument('-s', '--batchSize', default=1, 
                        help='used when queuing data')
    parser.add_argument('-d', '--diagnose', default=False, action='store_true', help='run simple test')
    parser.add_argument('command', default='help', nargs='?',
                        help='ingest | verify | createIngestTables | dropIngestTables | renameSchema ' \
                        + ' | createJoinedRows | createMetricsTable | dropMetricsTable ' \
                        + ' | populateMetricsTable | createDeltaRows | populateMetricsTableDelta ' \
                        + ' | runRowViewPipeline | runMetricsPipeline | createNewBibcodes ' \
                        + ' | runRowViewPipelineDelta | runMetricsPipelineDelta '\
                        + ' | runPipelines | runPipelinesDelta | nonbibToMasterPipeline | nonbibDeltaToMasterPipeline'
                        + ' | metricsToMasterPipeline')

    args = parser.parse_args()

    config.update(load_config())

    global logger
    logger = setup_logging('AdsDataSqlSync', config.get('LOG_LEVEL', 'INFO'))
    logger.info('starting AdsDataSqlSync.app with {}, {}'.format(args.command, args.fileType))
    nonbib_connection_string = config.get('INGEST_DATABASE',
                                   'postgresql://postgres@localhost:5432/postgres')
    nonbib_db_engine = create_engine(nonbib_connection_string)
    nonbib_db_conn = nonbib_db_engine.connect()

    metrics_connection_string = config.get('METRICS_DATABASE',
                                   'postgresql://postgres@localhost:5432/postgres')
    metrics_db_engine = create_engine(metrics_connection_string)
    metrics_db_conn = metrics_db_engine.connect()

    if args.command == 'help' and args.diagnose:
        diagnose_nonbib()
        diagnose_metrics()

    elif args.command == 'createIngestTables':
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.create_column_tables(nonbib_db_engine)

    elif args.command == 'dropIngestTables':
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.drop_column_tables(nonbib_db_engine)

    elif args.command == 'createJoinedRows':
        sql_sync = nonbib.NonBib(args.rowViewSchemaName, config)
        sql_sync.create_joined_rows(nonbib_db_conn)

    elif args.command == 'createMetricsTable' and args.metricsSchemaName:
        m = metrics.Metrics(args.metricsSchemaName)
        m.create_metrics_table(metrics_db_engine)

    elif args.command == 'dropMetricsTable' and args.metricsSchemaName:
        m = metrics.Metrics(args.metricsSchemaName)
        m.drop_metrics_table(metrics_db_engine)

    elif args.command == 'populateMetricsTable' and args.rowViewSchemaName and args.metricsSchemaName:
        m = metrics.Metrics()
        m.update_metrics_all(metrics_db_conn, nonbib_db_conn, args.rowViewSchemaName)

    elif args.command == 'populateMetricsTableDelta' and args.rowViewSchemaName and args.metricsSchemaName:
        m = metrics.Metrics(args.metricsSchemaName, {'FROM_SCRATCH': False, 'COPY_FROM_PROGRAM': False})
        m.update_metrics_changed(args.rowViewSchemaName)

    elif args.command == 'renameSchema' and args.rowViewSchemaName and args.rowViewBaselineSchemaName:
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.rename_schema(nonbib_db_conn, args.rowViewBaselineSchemaName)

    elif args.command == 'createDeltaRows' and args.rowViewSchemaName and args.rowViewBaselineSchemaName:
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.create_delta_rows(nonbib_db_conn, args.rowViewBaselineSchemaName)

    elif args.command == 'createNewBibcodes' and args.rowViewSchemaName and args.rowViewBaselineSchemaName:
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.build_new_bibcodes(nonbib_db_conn, args.rowViewBaselineSchemaName)

    elif args.command == 'logDeltaReasons' and args.rowViewSchemaName and args.rowViewBaselineSchemaName:
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.log_delta_reasons(nonbib_db_conn, args.rowViewBaselineSchemaName)

    elif args.command == 'runRowViewPipeline' and args.rowViewSchemaName:
        # drop tables, create tables, load data, create joined view
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.drop_column_tables(nonbib_db_engine)
        sql_sync.create_column_tables(nonbib_db_engine)
        load_column_files(config, nonbib_db_engine, nonbib_db_conn, sql_sync)

    elif args.command == 'runMetricsPipeline' and args.rowViewSchemaName and args.metricsSchemaName:
        m = metrics.Metrics(args.metricsSchemaName)
        m.drop_metrics_table(metrics_db_engine)
        m.create_metrics_table(metrics_db_engine)
        m.update_metrics_all(metrics_db_conn, nonbib_db_conn, args.rowViewSchemaName)

    elif args.command == 'runRowViewPipelineDelta' and args.rowViewSchemaName and args.rowViewBaselineSchemaName:
        # read in flat files, compare to staging/baseline
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.drop_column_tables(nonbib_db_engine)
        sql_sync.create_column_tables(nonbib_db_engine)
       
        load_column_files(config, nonbib_db_engine, nonbib_db_conn, sql_sync)

        sql_sync.create_delta_rows(nonbib_db_conn, args.rowViewBaselineSchemaName)
        sql_sync.log_delta_reasons(nonbib_db_conn, args.rowViewBaselineSchemaName)

    elif args.command == 'runMetricsPipelineDelta' and args.rowViewSchemaName and args.metricsSchemaName:
        m = metrics.Metrics(args.metricsSchemaName)
        m.update_metrics_changed(metrics_db_conn, nonbib_db_conn, args.rowViewSchemaName)

    elif args.command == 'runPipelines' and args.rowViewSchemaName and args.metricsSchemaName:
        # drop tables, create tables, load data, compute metrics
        sql_sync = nonbib.NonBib(args.rowViewSchemaNameg)
        sql_sync.drop_column_tables(nonbib_db_engine)
        sql_sync.create_column_tables(nonbib_db_engine)
        load_column_files(config, nonbib_db_engine, nonbib_db_conn, sql_sync)

        m = metrics.Metrics(args.metricsSchemaName)
        m.drop_metrics_table(metrics_db_engine)
        m.create_metrics_table(metrics_db_engine)
        m.update_metrics_all(metrics_db_conn, nonbib_db_conn, args.rowViewSchemaName)

    elif args.command == 'runPipelinesDelta' and args.rowViewSchemaName and args.metricsSchemaName and args.rowViewBaselineSchemaName:
        # drop tables, rename schema, create tables, load data, compute delta, compute metrics
        baseline_sql_sync = nonbib.NonBib(args.rowViewBaselineSchemaName)
        baseline_sql_sync.drop_column_tables()
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.rename_schema(args.rowViewBaselineSchemaName)

        baseline_sql_sync = None
        sql_sync = nonbib.NonBib(args.rowViewSchemaName)
        sql_sync.create_column_tables(nonbib_db_engine)
       
        load_column_files(config, sql_sync)

        sql_sync.create_delta_rows(args.rowViewBaselineSchemaName)
        sql_sync.log_delta_reasons(args.rowViewBaselineSchemaName)

        m = metrics.Metrics(args.metricsSchemaName, {'FROM_SCRATCH': False, 'COPY_FROM_PROGRAM': False})
        m.update_metrics_changed(args.rowViewSchemaName)
    elif args.command == 'nonbibToMasterPipeline' and args.diagnose:
        diagnose_nonbib()
    elif args.command == 'nonbibToMasterPipeline':
        nonbib_to_master_pipeline(args.rowViewSchemaName, int(args.batchSize))
    elif args.command == 'nonbibDeltaToMasterPipeline':
        print 'diagnose = ', args.diagnose
        nonbib_delta_to_master_pipeline(args.rowViewSchemaName, int(args.batchSize))
    elif args.command == 'metricsToMasterPipeline' and args.diagnose:
        diagnose_metrics()
    elif args.command == 'metricsToMasterPipeline':
        metrics_to_master_pipeline(args.metricsSchemaName, int(args.batchSize))
    elif args.command == 'metricsDeltaToMasterPipeline':
        nonbib_delta_to_master_pipeline(args.metricsSchemaName, args.rowViewSchemaName, int(args.batchSize))

    else:
        print 'app.py: illegal command or missing argument, command = ', args.command
        print '  row view schema name = ', args.rowViewSchemaName
        print '  row view baseline schema name = ', args.rowViewBaselineSchemaName
        print '  metrics schema name = ', args.metricsSchemaName

    if nonbib_db_conn:
        nonbib_db_conn.close()
            
    logger.info('completed columnFileIngest with {}, {}'.format(args.command, args.fileType))


if __name__ == "__main__":
    main()
