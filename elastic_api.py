"""Reference example of the style of Elasticsearch/Kibana API wrapper we use
internally. Trimmed and simplified for this exercise.

You are NOT required to use this. It is here to show you the conventions we like:
basic auth from the environment, retry on transient 5xx errors, explicit
timeouts, and clear error surfacing. Feel free to build a cleaner client of your
own design — improving on this style is a plus.

Environment variables used:
    ELASTIC_URL      base URL of the ES HTTP API, e.g. http://localhost:9200
    ELASTIC_USER     (optional) basic-auth user
    ELASTIC_PASS     (optional) basic-auth password
    ELASTIC_VERIFY_TLS   (optional) "false"/"0"/"no" disables TLS certificate
                         verification (e.g. for a self-signed local cluster).
                         Defaults to "true" — verification is ON unless you
                         explicitly opt out. ONLY FOR HTTPS CONNECTION!
    ELASTIC_CA_CERT      (optional) path to a custom CA bundle to verify
                         against (e.g. a private/internal CA). If set, this
                         takes precedence over ELASTIC_VERIFY_TLS.   
"""
import logging
import os
import time

import requests

logging.basicConfig(
    format="[%(asctime)s]:%(levelname)s:%(name)s:%(message)s",
    level=logging.INFO,
)

MAX_RETRIES = 3

def tls_verify():
    ca_cert = os.environ.get("ELASTIC_CA_CERT")
    if ca_cert:
        return ca_cert
    else:
        verify_flag = os.environ.get("ELASTIC_VERIFY_TLS", "true").strip().lower()
        return verify_flag not in ("false", "0", "no")


def es_request(method, endpoint, data=None, params=None, timeout=300):
    """Call the Elasticsearch HTTP API and return the parsed JSON body.

    Retries up to MAX_RETRIES times on transient 5xx responses. Raises
    ValueError on a non-OK response or once retries are exhausted.
    """
    base = os.environ["ELASTIC_URL"].rstrip("/")
    endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
    url = base + endpoint

    auth = None
    if "ELASTIC_USER" in os.environ:
        auth = (os.environ["ELASTIC_USER"], os.environ.get("ELASTIC_PASS", ""))

    for attempt in range(MAX_RETRIES):
        logging.info("Elastic %s %s", method, url)
        res = requests.request(
            method,
            url=url,
            auth=auth,
            params=params,
            json=data,
            timeout=timeout,
            verify=tls_verify()
        )

        if res.status_code // 100 == 5:
            logging.error("Server error %s, retrying in 5s...", res.status_code)
            time.sleep(5)
            continue
        elif not res.ok:
            logging.error("Elastic API error %s: %s", res.status_code, res.text)
            raise ValueError(f"Elastic API error: {res.status_code}")
        elif res.status_code == 204:
            return {}
        
        try:
            return res.json()
        except ValueError:
            return res.text

    raise ValueError("Elastic API error: retries exceeded")
