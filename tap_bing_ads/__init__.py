#!/usr/bin/env python3

import inspect
import json
import sys
import re
from datetime import datetime

import singer
import pytz
from singer import utils
from bingads import AuthorizationData, OAuthWebAuthCodeGrant, ServiceClient
from suds.sudsobject import asdict

LOGGER = singer.get_logger()

REQUIRED_CONFIG_KEYS = [
    "start_date",
    "customer_id",
    "account_ids",
    "oauth_client_id",
    "oauth_client_secret",
    "refresh_token",
    "developer_token",
]

CONFIG = {}
STATE = {}

def create_sdk_client(service, customer_id, account_id):
    authentication = OAuthWebAuthCodeGrant(
        CONFIG['oauth_client_id'],
        CONFIG['oauth_client_secret'],
        '') ## redirect URL not needed for refresh token

    authentication.request_oauth_tokens_by_refresh_token(CONFIG['refresh_token'])

    authorization_data = AuthorizationData(
        account_id=account_id,
        customer_id=CONFIG['customer_id'],
        developer_token=CONFIG['developer_token'],
        authentication=authentication)

    return ServiceClient(service, authorization_data)

def sobject_to_dict(d):
    if not hasattr(d, '__keylist__'):
        return d

    out = {}
    for k, v in asdict(d).items():
        if hasattr(v, '__keylist__'):
            out[k] = sobject_to_dict(v)
        elif isinstance(v, list):
            out[k] = []
            for item in v:
                out[k].append(sobject_to_dict(item))
        elif isinstance(v, datetime):
            out[k] = utils.strftime(pytz.utc.localize(v))
        else:
            out[k] = v
    return out

def xml_to_json_type(xml_type):
    if xml_type == 'boolean':
        return 'boolean'
    elif xml_type in ['decimal', 'float', 'double']:
        return 'number'
    elif xml_type == 'long':
        return 'integer'
    elif xml_type in ['dateTime', 'date']:
        return 'string'
    else:
        return 'string'

def get_json_schema(element):
    types = []
    format = None
    enum = None

    if element.nillable:
        types.append('null')

    if element.root.name == 'simpleType':
        enum = list(map(lambda x: x.name, element.rawchildren[0].rawchildren))
        types.append('string')
    else:
        xml_type = element.type[0]

        _type = xml_to_json_type(xml_type)
        types.append(_type)

        if xml_type in ['dateTime', 'date']:
            format = 'date-time'

    schema = { 'type': types }
    
    if format:
        schema['format'] = format

    if enum:
        schema['enum'] = enum

    return schema

array_type_regex = r'ArrayOf([a-z]+)'

def get_array_type(array_type):
    xml_type = re.match(array_type_regex, array_type).groups()[0]
    json_type = xml_to_json_type(xml_type)

    return {
        'type': 'array',
        'items': {
            'type': json_type
        }
    }

def wsdl_type_to_schema(wsdl_type):
    if wsdl_type.root.name == 'simpleType':
        return get_json_schema(wsdl_type)

    properties = {}
    for element in wsdl_type.rawchildren[0].rawchildren:
        if element.root.name == 'enumeration':
            properties[element.name] = get_json_schema(element)
        elif element.type is None and element.ref:
            properties[element.name] = element.ref[0] ## set to service type name for now
        elif element.type[1] != 'http://www.w3.org/2001/XMLSchema': ## not a built-in XML type
            _type = element.type[0]
            if 'ArrayOfstring' in _type:
                properties[element.name] = get_array_type(_type)
            else:
                properties[element.name] = _type ## set to service type name for now
        else:
            properties[element.name] = get_json_schema(element)
    
    return {
        'type': ['object'],
        'additionalProperties': False,
        'properties': properties
    }

def fill_in_nested_types(type_map, schema):
    for prop, descriptor in schema['properties'].items():
        if isinstance(descriptor, str) and descriptor in type_map:
            schema['properties'][prop] = type_map[descriptor]

def get_type_map(client):
    type_map = {}
    for type_tuple in client.soap_client.sd[0].types:
        _type = type_tuple[0]
        qname = _type.qname[1]
        if 'https://bingads.microsoft.com' not in qname and \
           'http://schemas.datacontract.org' not in qname:
            continue
        type_map[_type.name] = wsdl_type_to_schema(_type)

    for type_name, schema in type_map.items():
        if 'properties' in schema:
            fill_in_nested_types(type_map, schema)
    
    return type_map

def get_stream_def(stream_name, pks, schema, replication_key=None):
    stream_def = {
        'tap_stream_id': stream_name,
        'stream': stream_name,
        'key_properties': pks,
        'schema': schema,
        'replication_method': 'FULL_TABLE'
    }

    if replication_key:
        stream_def['replication_key'] = replication_key
        stream_def['replication_method'] = 'INCREMENTAL'

    return stream_def

def discover_core_objects():
    streams = []

    client = ServiceClient('CustomerManagementService')
    type_map = get_type_map(client)

    account_schema = type_map['Account']
     ## TODO: replication_key=LastModifiedTime
    streams.append(get_stream_def('accounts', ['Id'], account_schema))

    client = ServiceClient('CampaignManagementService')
    type_map = get_type_map(client)

    campaign_schema = type_map['Campaign']
    streams.append(get_stream_def('campaigns', ['Id'], campaign_schema))

    ad_group_schema = type_map['AdGroup']
    streams.append(get_stream_def('ad_groups', ['Id'], ad_group_schema))

    ad_schema = type_map['Ad']
    streams.append(get_stream_def('ads', ['Id'], ad_schema))

    json.dump({'streams': streams}, sys.stdout, indent=2)

def discover_reports():
    pass

def do_discover():
    discover_core_objects()
    discover_reports()

## TODO: remove fields not selected?

def sync_account_stream(customer_id, account_id):
    client = create_sdk_client('CustomerManagementService', customer_id, account_id)
    response = client.GetAccount(AccountId=account_id)
    ## TODO: filter accounts based in LastModifiedTime
    singer.write_record('accounts', sobject_to_dict(response))

def sync_campaigns(client, account_id, selected_streams):
    response = client.GetCampaignsByAccountId(AccountId=account_id)
    response_dict = sobject_to_dict(response)
    if 'Campaign' in response_dict:
        campaigns = response_dict['Campaign']

        if 'campaigns' in selected_streams:
            for campaign in campaigns:
                singer.write_record('campaigns', campaign)

        return map(lambda x: x['Id'], campaigns)

def sync_ad_groups(client, campaign_ids, selected_streams):
    ad_group_ids = []
    for campaign_id in campaign_ids:
        response = client.GetAdGroupsByCampaignId(CampaignId=campaign_id)
        response_dict = sobject_to_dict(response)

        if 'AdGroup' in response_dict:
            ad_groups = sobject_to_dict(response)['AdGroup']

            if 'ad_groups' in selected_streams:
                for ad_group in ad_groups:
                    singer.write_record('ad_groups', ad_group)

            ad_group_ids.append(list(map(lambda x: x['Id'], ad_groups)))
    return ad_group_ids

def sync_ads(client, ad_group_ids):
    for ad_group_id in ad_group_ids:
        response = client.GetAdsByAdGroupId(
            AdGroupId=ad_group_id,
            AdTypes={
                'AdType': [
                    'AppInstall',
                    'DynamicSearch',
                    'ExpandedText',
                    'Product',
                    'Text',
                    'Image'
                ]
            })
        response_dict = sobject_to_dict(response)

        if 'Ad' in response_dict:
            for ad in response_dict['Ad']:
                singer.write_record('ads', ad)

def sync_core_objects(customer_id, account_id, selected_streams):
    if 'accounts' in selected_streams:
        sync_account_stream(customer_id, account_id)

    client = create_sdk_client('CampaignManagementService', customer_id, account_id)

    campaign_ids = sync_campaigns(client, account_id, selected_streams)

    if campaign_ids and ('ad_groups' in selected_streams or 'ads' in selected_streams):
        ad_group_ids = sync_ad_groups(client, campaign_ids, selected_streams)
        if 'ads' in selected_streams:
            sync_ads(client, ad_group_ids)

def sync_account(customer_id, account_id, selected_streams):
    sync_core_objects(customer_id, account_id, selected_streams)

def do_sync_all_accounts(customer_id, account_ids, catalog):
    selected_streams = list(map(lambda x: x.stream, catalog.streams))

    for account_id in account_ids:
        sync_account(customer_id, account_id, selected_streams)

def main_impl():
    args = utils.parse_args(REQUIRED_CONFIG_KEYS)

    CONFIG.update(args.config)
    STATE.update(args.state)
    account_ids = CONFIG['account_ids'].split(",")

    if args.discover:
        do_discover()
        LOGGER.info("Discovery complete")
    elif args.catalog:
        do_sync_all_accounts(CONFIG['customer_id'], account_ids, args.catalog)
        LOGGER.info("Sync Completed")
    else:
        LOGGER.info("No catalog was provided")

## TODO:
## - Account TimeZone?
## - Use Campaign.TimeZone for reporting timezone?

def main():
    try:
        main_impl()
    except Exception as exc:
        LOGGER.critical(exc)
        raise exc

if __name__ == "__main__":
    main()
