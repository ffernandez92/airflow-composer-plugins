from airflow.plugins_manager import AirflowPlugin
from flask import Blueprint, request
from flask_admin import BaseView, expose
from airflow.contrib.hooks.gcp_api_base_hook import GoogleCloudBaseHook
from google.oauth2 import service_account
from airflow.models import Variable
import sqlalchemy
import airflow.configuration as config
import logging
import googleapiclient.discovery
import json

BASE_URL = "https://console.cloud.google.com/dataflow/jobs/"
project = Variable.get("gcp_project_id")
location = Variable.get("gcp_dataproc_region")


def get_cred():
    """
    Get GCP credentials from the Airflow connection `gcp_streaming_plugin_conn`
    :return: Credentials Object
    """
    gcp_hook = GoogleCloudBaseHook(gcp_conn_id='gcp_streaming_plugin_df_conn')
    json_str = json.loads(gcp_hook._get_field('keyfile_dict'))
    return service_account.Credentials.from_service_account_info(json_str)


dataflow = googleapiclient.discovery.build('dataflow', 'v1b3', credentials=get_cred())


def execute_sql_statement(sql_st):
    """
    Executes a SQL statement and returns a dictionary with the final result if any.
    :param sql_st: SQL Query to execute
    :return: dictionary
    """
    try:
        proxy_db_url = config.conf.get('core', 'sql_alchemy_conn')
        engine = sqlalchemy.create_engine(proxy_db_url, echo=True)
        result_list = []
        for result in engine.execute(sql_st):
            result_list.append(dict(result))
        return result_list
    except Exception:
        logging.info("Unable to interact with CloudSQL")


def init_application():
    """
    Creates the initial databases and tables necessary for the application to work.
    :return:
    """
    execute_sql_statement("CREATE DATABASE IF NOT EXISTS dataflow_jb_db")
    execute_sql_statement("CREATE TABLE IF NOT EXISTS dataflow_jb_db.jobs_db_d (job_name VARCHAR(1500) NOT NULL, "
                          "job_id VARCHAR(1500) NOT NULL, parameters TEXT NOT NULL, "
                          "monitor BOOLEAN DEFAULT false)")


def is_dataflow_job_running(job_id):
    """
    Queries the Dataflow REST API to check if the job_id is running or not.
    :param job_id: unique job identifier
    :return: true if the job is running false otherwise.
    """
    try:
        status = dataflow.projects().locations().jobs().get(projectId=project, jobId=job_id,
                                                            location=location).execute()
        if "RUNNING" in status['currentState']:
            return "true"
        else:
            return "false"
    except Exception:
        return "false"


def get_dataflow_jobs():
    """
    Gets all available Dataflow jobs that are on the database.
    :return: jobs dictionary
    """
    jobs_dic = execute_sql_statement("SELECT job_id, job_name, parameters, monitor FROM dataflow_jb_db.jobs_db_d")
    return jobs_dic


class DataflowView(BaseView):

    @expose('/')
    def initial_method(self):
        """
        Starts the app
        :return:
        """
        init_application()
        dict_jobs = get_dataflow_jobs()
        data_list = []
        try:
            for job in dict_jobs:
                url = BASE_URL + location + "/" + job.get('job_id') + "?project=" + project
                is_running = is_dataflow_job_running(job.get('job_id'))
                data_list.append({'column_a': job.get('job_id'), 'column_b': job.get('job_name'), 'column_c': is_running,
                                  'column_d': job.get('monitor'), 'column_e': url, 'column_f': str(job.get('parameters'))})
        except Exception:
            logging.info("Error on loading data from DF BD")

        return self.render("plugin_view/streaming_dataflow.html", data=data_list)

    @expose('/job_running/')
    def dataflow_running_status(self):
        """
        Check whether the job is running or not.
        :return: true if running false otherwise.
        """
        job_id = request.args.get("job_id")
        return is_dataflow_job_running(job_id)

    @expose('/job_monitor_active/')
    def dataflow_list_monitoring_jobs(self):
        """
        Gets all the jobs with monitor status equals true.
        :return: Jobs dictionary
        """
        jobs_list_dic = execute_sql_statement(
            "SELECT job_id, job_name FROM dataflow_jb_db.jobs_db_d WHERE monitor = true")
        job_list_result = []

        for jobs_dict in jobs_list_dic:
            dataflow_metrics = {}
            try:
                dataflow_metrics = dataflow.projects().locations().jobs().getMetrics(projectId=project,
                                                                                     location=location, jobId=jobs_dict[
                        "job_id"]).execute()
            except Exception:
                logging.info("E")

            temp_dic = {
                "job_id": jobs_dict["job_id"],
                "job_name": jobs_dict["job_name"],
                "is_running": is_dataflow_job_running(jobs_dict["job_id"]),
                "dataflow_metrics": dataflow_metrics
            }
            job_list_result.append(temp_dic)

        return str(job_list_result)

    @expose('/job_monitor/', methods=['POST'])
    def dataflow_set_monitor(self):
        """
        Changes the job monitor status. From false to true or vice versa
        :return:
        """
        parameters = request.get_json().get('parameters')
        job_id = parameters.get('job_id')
        monitor_status = parameters.get('monitor')
        monitor = "false"
        if monitor_status is True:
            monitor = "true"

        execute_sql_statement(
            "UPDATE dataflow_jb_db.jobs_db_d SET monitor = {monitor} WHERE job_id = '{job_id}'".format(monitor=monitor,
                                                                                                       job_id=job_id))

        return str(parameters)

    # This method does not stops the job execution. It just deletes the job from the database.
    @expose('/job_remove/', methods=['POST'])
    def dataflow_remove_job(self):
        """
        Removes the job from the jobs table.
        :return: unique job id.
        """
        job_id = request.get_json().get('parameters')
        execute_sql_statement("DELETE FROM dataflow_jb_db.jobs_db_d WHERE job_id = '{job_id}'".format(job_id=job_id))
        return str(job_id)

    @expose('/job_execute/', methods=['POST'])
    def dataflow_execute_templace(self):
        """
        Execute the user template that triggers the dataflow job
        :return: dictionary with the resulting parameters
        """
        parameters = request.get_json().get('parameters')
        try:
            job_name = parameters.pop('job_name')
            job_template_path = parameters.pop('job_template')
            job_sub_network = parameters.pop('job_sub_network')
            job_labels = parameters.pop('job_labels')
            job_machine = parameters.pop('job_machine')
            job_max_workers = parameters.pop('job_max_workers')

            environment = {}

            if job_machine:
                environment["machineType"] = job_machine

            if job_max_workers:
                environment["maxWorkers"] = int(job_max_workers)

            if job_sub_network:
                environment["subnetwork"] = job_sub_network

            if job_labels:
                arr_labels = job_labels.split(",")
                rest = {}
                for el_lab in arr_labels:
                    label = el_lab.split(":")
                    rest[label[0]] = label[1]
                environment["additionalUserLabels"] = rest

            body_dict = {
                "jobName": job_name,
                "parameters": parameters,
                "environment": environment
            }

            result = dataflow.projects().locations().templates().launch(projectId=project, location=location,
                                                                        body=body_dict,
                                                                        gcsPath=job_template_path).execute()
            job_id = result.get('job').get('id')
            body_dict["templatePath"] = job_template_path
            statement = """INSERT INTO dataflow_jb_db.jobs_db_d (job_name, job_id, parameters, monitor) 
            VALUES ('{job_name}', '{job_id}', "{parameters}", false)""".format(
                job_id=job_id, job_name=job_name, parameters=str(body_dict))
            execute_sql_statement(statement)
            return str(body_dict)
        except Exception as e:
            return str(e)


admin_view_ = DataflowView(category="Streaming", name="Dataflow")

blue_print_ = Blueprint("streaming_dataflow",
                        __name__,
                        template_folder='templates',
                        static_folder='static',
                        static_url_path='/static/streaming_dataflow')


class AirflowTestPlugin(AirflowPlugin):
    name = "streaming_dataflow"
    admin_views = [admin_view_]
    flask_blueprints = [blue_print_]
