# coding: utf-8
"""
:mod:`boardgamegeek.utils` - Generic helper functions
=====================================================

.. module:: boardgamegeek.utils
   :platform: Unix, Windows
   :synopsis: generic helper functions

.. moduleauthor:: Cosmin Luță <q4break@gmail.com>

"""
from __future__ import unicode_literals
import sys
import xml.etree.ElementTree as ET
from xml.etree.ElementTree import ParseError as ETParseError
import requests_cache
import requests
import logging
import time


try:
    import urllib.parse as urlparse
except:
    import urlparse

from .exceptions import BoardGameGeekAPIError, BoardGameGeekAPIRetryError, BoardGameGeekError
from .exceptions import BoardGameGeekAPINonXMLError, BoardGameGeekTimeoutError

log = logging.getLogger("boardgamegeek.utils")


class DictObject(object):
    """
    Just a fancy wrapper over a dictionary
    """

    def __init__(self, data):
        self._data = data

    def __getattr__(self, item):
        # allow accessing user's variables using .attribute
        if item != "_data" and item in self._data:
            return self._data[item]
        raise AttributeError

    def data(self):
        """
        Access to the internal data dictionary, for easy dumping
        :return: the internal data dictionary
        """
        return self._data


def xml_subelement_attr(xml_elem, subelement, convert=None, attribute="value", default=None, quiet=False):
    """
    Search for a sub-element and return the value of its attribute.

    For the following XML document:

    .. code-block:: xml

        <xml_elem>
            <subelement value="THIS" />
        </xml_elem>

    a call to ``xml_subelement_attr(xml_elem, "subelement")`` would return ``"THIS"``


    :param xml_elem: search the children nodes of this element
    :param subelement: Name of the sub-element to search for
    :param convert: if not None, a callable to perform the conversion of this attribute to a certain object type
    :param attribute: name of the attribute to get
    :param default: default value if the subelement or attribute is not found
    :param quiet: if True, don't raise exception from conversions, return default instead
    :return: value of the attribute or ``None`` in error cases

    """
    if xml_elem is None or not subelement:
        return None

    subel = xml_elem.find(subelement)
    if subel is None:
        value = default
    else:
        value = subel.attrib.get(attribute)
        if value is None:
            value = default
        elif convert:
            try:
                value = convert(value)
            except:
                if quiet:
                    value = default
                else:
                    raise
    return value


def xml_subelement_attr_list(xml_elem, subelement, convert=None, attribute="value", default=None, quiet=False):
    """
    Search for sub-elements and return a list of the specified attribute.

    .. code-block:: xml

        <xml_elem>
            <subelement value="THIS" />
            <subelement value="THIS2" />
            ...
        </xml_elem>

    For the above document, ["THIS", "THIS2"] will be returned

    :param xml_elem: search the children nodes of this element
    :param subelement: name of the sub-element to search for
    :param convert: if not None, a callable used to perform the conversion of this attribute to a certain object type
    :param attribute: name of the attribute to get
    :param default: default value to use if an attribute is missing
    :param quiet: if True, don't raise exceptions from conversions, instead use the default value
    :return: list containing the values of the attributes or ``None`` in error cases
    """
    if xml_elem is None or not subelement:
        return None

    subel = xml_elem.findall(subelement)
    res = []
    for e in subel:
        value = e.attrib.get(attribute)
        if value is None:
            value = default
        elif convert:
            try:
                value = convert(value)
            except:
                if quiet:
                    value = default
                else:
                    raise
        res.append(value)

    return res


def xml_subelement_text(xml_elem, subelement, convert=None, default=None, quiet=False):
    """
    Return the text of the specified subelement

    For the document below:

    .. code-block:: xml

        <xml_elem>
            <subelement>text</subelement>
        </xml_elem>

    ``"text"`` will be returned

    :param xml_elem: search the children nodes of this element
    :param subelement: name of the subelement whose text will be retrieved
    :param convert: if not None, a callable used to perform the conversion of the text to a certain object type
    :param default: default value if subelement is not found
    :param quiet: if True, don't raise exceptions from conversions, instead use the default value
    :return: The text associated with the sub-element or ``None`` in case of error
    """
    if xml_elem is None or not subelement:
        return None

    subel = xml_elem.find(subelement)
    if subel is None:
        text = default
    else:
        text = subel.text
        if text is None:
            text = default
        elif convert:
            try:
                text = convert(text)
            except:
                if quiet:
                    text = default
                else:
                    raise
    return text


def get_parsed_xml_response(requests_session, url, params=None, timeout=15, retries=3, retry_delay=5):
    """
    Downloads an XML from the specified url, parses it and returns the xml ElementTree.

    :param requests_session: A Session of the ``requests`` library, used to fetch the url
    :param url: the address where to get the XML from
    :param params: dictionary containing the parameters which should be sent with the request
    :param timeout: number of seconds after which the request times out
    :param retries: number of retries to perform in case of timeout
    :param retry_delay: the amount of seconds to sleep when retrying an API call that returned 202
    :return: :py:func:`xml.etree.ElementTree` corresponding to the XML
    :raises: :py:class:`BoardGameGeekAPINonXMLError` if the API response wasn't XML
    :raises: :py:class:`BoardGameGeekAPIRetryError` if this request should be retried after a short delay
    :raises: :py:class:`BoardGameGeekAPIError` if the response couldn't be parsed
    :raises: :py:class:`BoardGameGeekTimeoutError` if there was a timeout
    """

    retr = retries - 1

    # retry loop
    while retr >= 0:

        try:
            r = requests_session.get(url, params=params, timeout=timeout)

            if r.status_code == 202:
                if retries == 0:
                    # no retries have been requested, therefore raise exception to signal the application that it
                    # needs to retry
                    # (BoardGameGeek API says that on status code 202 the call should be retried after a delay)
                    raise BoardGameGeekAPIRetryError()
                elif retr == 0:
                    # retries were requested, but we reached 0. Signal the application that it needs to retry itself.
                    raise BoardGameGeekAPIRetryError("failed to retrieve data after {} retries".format(retries))
                else:
                    # sleep for the specified delay and retry
                    log.debug("API call will be retried in {} seconds ({} more retries)".format(retry_delay, retr))
                    retr -= 1
                    time.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
            elif r.status_code == 503:
                # it seems they added some sort of protection which triggers when too many requests are made, in which
                # case we get back a 503. Try to delay and retry
                log.warning("API returned 503, retrying")
                retr -= 1
                time.sleep(retry_delay)
                retry_delay *= 1.5
                continue

            if not r.headers.get("content-type").startswith("text/xml"):
                raise BoardGameGeekAPINonXMLError("non-XML reply")

            xml = r.text

            if sys.version_info >= (3,):
                root_elem = ET.fromstring(xml)
            else:
                utf8_xml = xml.encode("utf-8")
                root_elem = ET.fromstring(utf8_xml)

            return root_elem

        except requests.exceptions.Timeout:
            if retries == 0:
                raise BoardGameGeekTimeoutError()
            elif retr == 0:
                # ... reached 0 retries
                raise BoardGameGeekTimeoutError("failed to retrieve data after {} retries".format(retries))
            else:
                log.debug("API request timeout, retrying {} more times w/timeout {}".format(retr, timeout))
                timeout *= 2.5
                retr -= 1
                continue

        except ETParseError as e:
            raise BoardGameGeekAPIError("error decoding BGG API response: {}".format(e))

        except (BoardGameGeekAPIRetryError, BoardGameGeekAPINonXMLError, BoardGameGeekTimeoutError):
            raise

        except Exception as e:
            raise BoardGameGeekAPIError("error fetching BGG API response: {}".format(e))

    raise BoardGameGeekAPIError("couldn't fetch data within the configured number of retries")


def get_cache_session_from_uri(uri):
    """
    Returns a requests-cache session using caching specified in the URI. Valid uris are:

    * memory:///?ttl=<seconds>
    * sqlite:///path/to/sqlite.db?ttl=<seconds>&fast_save=<0|1>

    :param uri: URI specifying the type of cache to use and its parameters
    :return: CachedSession instance, which can be used as a regular ``requests`` session.
    :raises: :class:`BoardGameGeekError` in case of error
    """

    try:
        r = urlparse.urlparse(uri)

        args = urlparse.parse_qs(r.query)

        # if not specified, default cache time is 3600 seconds
        ttl = int(args.get("ttl", ['3600'])[0])

        if r.scheme == "memory":
            return requests_cache.core.CachedSession(backend="memory",
                                                     expire_after=ttl,
                                                     allowable_codes=(200,))

        elif r.scheme == "sqlite":
            fast_save = args.get("fast_save", ["0"])[0] != "0"
            return requests_cache.core.CachedSession(cache_name=r.path,
                                                     backend="sqlite",
                                                     expire_after=ttl,
                                                     extension="",
                                                     fast_save=fast_save,
                                                     allowable_codes=(200,))

        # TODO: add the redis backend
        # elif r.scheme == "redis":
        #     return requests_cache.core.CachedSession(cache_name=args.get("prefix", ["cache"])[0],
        #                                              backend="redis",
        #                                              expire_after=ttl,
        #                                              allowable_codes=(200,))

        # TODO: add the mongo backend

    except Exception as e:
        raise BoardGameGeekError("error trying to create a CachedSession from '{}': {}".format(uri, e))

    raise BoardGameGeekError("invalid cache URI: {}".format(uri))


