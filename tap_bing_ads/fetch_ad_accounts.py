import requests
import logging
import xmltodict

from collections import OrderedDict
from requests.exceptions import HTTPError

logger = logging.getLogger(__name__)

SOAP_CUSTOMER_MANAGEMENT_URL = "https://clientcenter.api.bingads.microsoft.com/Api/CustomerManagement/v13/CustomerManagementService.svc"


def get_field(*fields, obj: dict, default=None):
    for field in fields:
        obj = obj.get(field)
        if obj is None:
            return default

    return obj


def _request(headers: dict, data: str):
    response = requests.post(SOAP_CUSTOMER_MANAGEMENT_URL, headers=headers, data=data,)

    response.raise_for_status()

    response_decoded = response.content.decode("utf-8")
    response_xml_parsed = xmltodict.parse(response_decoded)

    return response_xml_parsed


def _request_customer_id(access_token: str, developer_token: str) -> int:
    response = _request(
        headers={"content-type": "text/xml", "SOAPAction": "GetCustomersInfo"},
        data=f"""
            <s:Envelope xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
                <s:Header xmlns="https://bingads.microsoft.com/Customer/v13">
                    <Action mustUnderstand="1">GetCustomersInfo</Action>
                    <AuthenticationToken i:nil="false">{access_token}</AuthenticationToken>
                    <DeveloperToken i:nil="false">{developer_token}</DeveloperToken>
                </s:Header>
                <s:Body>
                    <GetCustomersInfoRequest xmlns="https://bingads.microsoft.com/Customer/v13">
                        <CustomerNameFilter i:nil="false"></CustomerNameFilter>
                        <TopN>1</TopN>
                    </GetCustomersInfoRequest>
                </s:Body>
            </s:Envelope>
        """,
    )

    customer_id = get_field(
        "s:Envelope",
        "s:Body",
        "GetCustomersInfoResponse",
        "CustomersInfo",
        "a:CustomerInfo",
        "a:Id",
        obj=response,
    )

    return int(customer_id)


def _request_ad_accounts(
    customer_id: int, access_token: str, developer_token: str
) -> list:
    response = _request(
        headers={"content-type": "text/xml", "SOAPAction": "GetAccountsInfo"},
        data=f"""
            <s:Envelope xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
                <s:Header xmlns="https://bingads.microsoft.com/Customer/v13">
                    <Action mustUnderstand="1">GetAccountsInfo</Action>
                    <AuthenticationToken i:nil="false">{access_token}</AuthenticationToken>
                    <DeveloperToken i:nil="false">{developer_token}</DeveloperToken>
                </s:Header>
                <s:Body>
                    <GetAccountsInfoRequest xmlns="https://bingads.microsoft.com/Customer/v13">
                        <CustomerId i:nil="false">{customer_id}</CustomerId>
                        <OnlyParentAccounts>false</OnlyParentAccounts>
                    </GetAccountsInfoRequest>
                </s:Body>
            </s:Envelope>
        """,
    )

    ad_account_or_accounts = get_field(
        "s:Envelope",
        "s:Body",
        "GetAccountsInfoResponse",
        "AccountsInfo",
        "a:AccountInfo",
        obj=response,
        default=[],
    )

    if isinstance(ad_account_or_accounts, (OrderedDict, dict)):
        return [ad_account_or_accounts]

    return ad_account_or_accounts


def fetch_ad_accounts(access_token: str, developer_token: str):
    customer_id = _request_customer_id(access_token, developer_token)

    ad_accounts = _request_ad_accounts(customer_id, access_token, developer_token)

    result_ad_accounts = [ad_account["a:Id"] for ad_account in ad_accounts]

    return customer_id, result_ad_accounts
