'''
Copyright © 2020, SAS Institute Inc., Cary, NC, USA.  All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
'''

import requests
import gzip
import codecs
import base64
import jwt
import os
from flask import Flask, request
from google.cloud import storage
from datetime import datetime, timedelta


def read_config(config_file):
    keys = {}
    separator = '='
    with open(config_file) as f:
        for line in f:
            if line.startswith('#') == False and separator in line:
                # Find the name and value by splitting the string
                name, value = line.split(separator, 1)
                # Assign key value pair to dict
                keys[name.strip()] = value.strip()
    return keys


config = read_config('config/config.txt')

# Google Cloud Platform Settings
# Credential file location - https://cloud.google.com/iam/docs/creating-managing-service-account-keys
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = config['credentials_file_path']
# Bucket location - must be already created
bucket_name = config['bucket_name']

# server details and credentials
tenant_secret = config['tenant_secret']
tenant_id = config['tenant_id']
gateway_url = config['gateway_url']


# default values
delimiter = ','
soh_delimiter = "\x01"

# temporary folder location
dir_csv = '/temp/csv/'
dir_zip = '/temp/zip/'
dir_config = '/temp/'
dir_extr = '/temp/'

app = Flask(__name__)


# server runs on location /
@app.route('/')
def connector_invocation():
    table_name = request.args.get('tableName')
    print('\n Processing the table ', table_name)
    yesterday = datetime.today() - timedelta(days=1)
    yesterday_start_timestamp = yesterday.strftime('%Y-%m-%dT00:00:00.000Z')
    yesterday_end_timestamp = yesterday.strftime('%Y-%m-%dT11:59:59.000Z')
    url = gateway_url + "discoverService/udmFetchTableData?tableName=" + table_name + \
          "&startTimeStamp=" + yesterday_start_timestamp + \
          "&endTimeStamp=" + yesterday_end_timestamp
    pull_data(url)
    return 'Success!'


def pull_data(url):
    csv_file = dir_csv + 'final.csv'
    # Generate the JWT
    encoded_secret = base64.b64encode(bytes(tenant_secret, 'utf-8'))
    token = jwt.encode({'clientID': tenant_id}, encoded_secret, algorithm='HS256')
    print('\nJWT token: ' + bytes.decode(token))
    headers = {'authorization': "Bearer " + bytes.decode(token), 'cache-control': "no-cache"}

    # Fetch the data from the server
    print('\n Fetching URL ' + url)
    response_obj = requests.get(url, headers=headers)

    print('\n The response is ' + response_obj.text)
    response_json = response_obj.json()

    csv_header = generate_csv_header_from_schema(response_json['schema'])
    process_data(response_json['items'], csv_header, csv_file)

    upload_blob_to_gcp(csv_file)


def generate_csv_header_from_schema(schema_json):
    column_header = ''
    for item in schema_json:
        column_name = item['column_name']
        column_header = column_header + column_name + delimiter

    # remove last delimiter and return line
    return column_header[:-len(delimiter)]


def process_data(items, header, csv_file):
    i = 0
    for item in items:
        if item['dataRangeProcessingStatus'] == "DATA_AVAILABLE":
            # when its first file then create a new file else append to existing file
            if i >= 1:
                header = None
            i = i + 1
            fetch_and_download_data(item['entities'], header, csv_file)
            print("data available")
        else:
            print("No data available")


def fetch_and_download_data(entities, header, csv_file):
    unzipped_file_location = dir_zip + 'unzipped' + '.soh'
    i = 0
    for entity in entities:
        data_urls = entity['dataUrlDetails']
        for data_url in data_urls:
            if i >= 1:
                header = None
            i = i + 1
            url_to_download = data_url['url']
            download_unzip_extract(url_to_download, unzipped_file_location)
            write_csv(unzipped_file_location, csv_file, soh_delimiter, delimiter, header)


def download_unzip_extract(url_of_zip, unzipped_file):
    print("downloading.. ", url_of_zip)
    zipped_file = dir_extr + 'temp' + '.gz'
    stream = requests.get(url_of_zip)
    with open(zipped_file, 'wb') as f:
        f.write(stream.content)
    with gzip.open(zipped_file, "rb") as zipped, \
            open(unzipped_file, "wb") as unzipped:
        # read zipped data
        unzipped_content = zipped.read()
        # save unzipped data into file
        unzipped.write(unzipped_content)
        print("downloaded and unzipped at ", unzipped_file)


def write_csv(in_file, out_file, in_delimiter, out_delimiter, header=None):
    if header is not None:
        write_mode = 'w'
    else:
        write_mode = 'a'

    print("write_mode", write_mode)
    with codecs.open(in_file, 'r', 'utf-8') as in_f, \
            codecs.open(out_file, write_mode, 'utf-8') as out_f:
        # print column header line in csv file if flag is yes
        if header is not None:
            out_f.write(header + "\n")
        # go line by line and replace delimiter
        rows = 0
        for line in in_f:
            rows = rows + 1
            try:
                out_f.write(line.replace(in_delimiter, out_delimiter))
            except UnicodeEncodeError as e:
                print('\n Error in row: ' + str(rows) + ' - ' + str(e))


def upload_blob_to_gcp(source_file_name):
    yesterday = datetime.today() - timedelta(days=1)
    destination_blob_name = "udm-" + yesterday.strftime('%Y-%m-%d')

    storage_client = storage.Client()
    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(destination_blob_name)

    blob.upload_from_filename(source_file_name)

    print(
        "\n File {} uploaded to {}.".format(
            source_file_name, destination_blob_name
        )
    )

