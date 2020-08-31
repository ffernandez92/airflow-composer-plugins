# airflow-composer-plugins
Airflow Composer Plugins

## Dataflow Streaming Plugin
This web plugin allows to use Dataflow Templates from Airflow UI.

![Image of the plugin UI](https://github.com/ffernandez92/airflow-composer-plugins/blob/master/img/dfm.png)

### Configuration:
You must create the following Variables on the Variables Airflow interface.
* gcp_project_id : name of the GCP project.
* gcp_dataproc_region : name of the GCP region where your Dataflow jobs are going to run.

Also, we need to set up a new GCP connection with the right JSON file credentials:

* gcp_streaming_plugin_df_conn : Airflow connection credentials.
