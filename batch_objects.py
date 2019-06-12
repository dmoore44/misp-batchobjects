#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
TODO:
* Add a mechanism to add to_ids and correlate
* Add mechanism to add relations between objects
* Add option to export the new Event as JSON --dryrun
* Load Excel files
"""

import csv
import argparse, logging, sys, os, configparser
import pprint

pprint = pprint.PrettyPrinter(indent=4).pprint
script_path = os.path.dirname(os.path.realpath(__file__))

log = logging.getLogger('BatchObjects')
LOGGING_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'

config = configparser.ConfigParser(inline_comment_prefixes=(';',))
config_path = '{}/config.ini'.format(script_path)
try:
    config.read(config_path)
except Exception as e:
    print('Error loading {}, copy the config.ini.sample: {}'.format(config_path, e))
    exit(1)

from pymisp import PyMISP, MISPEvent
from pymisp.tools import GenericObjectGenerator
from pymisp.exceptions import NewAttributeError

def get_object_fields(csv_path, delim, quotechar, strictcsv):

    objects = []
    for csvfile in csv_path:
        objects_file = csv.DictReader(
            open(os.path.abspath(csvfile)),
            delimiter=delim,
            quotechar=quotechar,
            strict=strictcsv
        )

        for row in objects_file:
            if row['object'] == '' or row['object'].startswith('#'):
                continue

            obj = {
                'data': []
            }

            for field, value in row.items():
                if field == 'object':
                    obj['object'] = value.lower()
                elif field == 'distribution':
                    obj['distribution'] = value
                elif field == 'comment':
                    obj['comment'] = value
                elif value:
                    field = str(field.split('__')[0].lower())
                    obj['data'].append({field:value})
    
            objects.append(obj)

    return objects

if __name__ == '__main__':

    parser = argparse.ArgumentParser(description='Upload a CSV of OBJECTS to an EVENT')

    # # MISP options
    parser.add_argument('--misp_url', dest='misp_url', metavar='"http://misp.local"', default=config['MISP'].get('url'), help='MISP URL (overrides conf.ini)')
    parser.add_argument('--misp_key', dest='misp_key', metavar='<API_KEY>', default=config['MISP'].get('key'), help='MISP API key (overrides conf.ini)')
    parser.add_argument('--misp_validate_cert', dest='misp_validate_cert', action='store_true', default=config['MISP'].getboolean('validate_cert'), help='Validate MISP SSL certificate (overrides conf.ini)')
    parser.add_argument('--custom_objects', metavar='/path/to/objects/dir/', dest='custom_objects_path', default=config['MISP'].get('custom_objects_path'), help='If using custom objects, provide the path to the object json (overrides conf.ini)')

    # # CSV options
    parser.add_argument('--delim', metavar='","', default=config['CSV_READER'].get('delimiter'), type=str, help='CSV delimiter')
    parser.add_argument('--quotechar', metavar='"\'"', default=config['CSV_READER'].get('quote_character'), type=str, help='CSV quote character')
    parser.add_argument('--strictcsv', default=config['CSV_READER'].getboolean('strict_csv_parsing'), action='store_false', help='Strict loading of the CSV')
    parser.add_argument('--dryrun', default=False, action='store_true', help='Show objects before sending to MISP')
    parser.add_argument('-v', '--verbose', default=False, action='store_true', help='Print debug information to stderr')

    # # Event creation options
    misp_group = parser.add_mutually_exclusive_group(required=True)
    misp_group.add_argument('-e', '--event', metavar='(int|uuid)', type=str, help='EVENT to add the objects to.')
    misp_group.add_argument('-i', '--info', metavar='"Title for new event" ...', type=str, help="Info field if a new event is to be created")
    parser.add_argument('--dist', '--distribution', dest='distribution', metavar='[0-4]', default=config['MISP'].getint('default_distribution'), type=int, help='Event distribution level - New events ONLY (--info) (overrides conf.ini)')

    # # CSV to parse option
    parser.add_argument('-c', '--csv', metavar='/path/to/file.csv', nargs='+', required=True, type=str, help='CSV to create the objects from')
    args = parser.parse_args()

    # # Args tests
    if args.verbose or ('DEBUG' in os.environ):
        args.verbose = True # Could have been set by the environment
        pymisp_logger = logging.getLogger('pymisp')
        pymisp_logger.setLevel(logging.DEBUG)
        logging.basicConfig(stream=sys.stderr, format=LOGGING_FORMAT, level=logging.DEBUG)
    else:
        # # urllib3 is noisy
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        logging.basicConfig(stream=sys.stderr, format=LOGGING_FORMAT, level=logging.INFO)

    # # Connect to MISP
    pymisp = PyMISP(args.misp_url, args.misp_key, args.misp_validate_cert, debug=args.verbose)

    # # Get current object templates
    template = pymisp.get_object_templates_list()
    if 'response' in template.keys():
        template = template['response']
    else:
        log.critical('Could not get templates from MISP!')
        exit(1)

    # # Load objects from the CSV file
    objects = get_object_fields(args.csv, args.delim, args.quotechar, args.strictcsv)

    # # Create a new Event
    if args.info:
        event = MISPEvent()
        event.info = args.info
        if args.distribution:
            log.debug('Setting distribution level for Event: {}'.format(args.distribution))
            event.distribution = args.distribution

        if not args.dryrun:
            new_event = pymisp.add_event(event)

            if 'errors' in new_event.keys():
                log.critical('Error creating the new event. Error: {}'.format('; '.join(new_event['errors'])))
                exit(1)

            # # Get the ID of the new event for later
            args.event = new_event['Event']['uuid']
            log.info('New event created: {}'.format(args.event))

    # # Add Objects to existing Event
    for o in objects:
        misp_object = GenericObjectGenerator(o['object'],  misp_objects_path_custom=args.custom_objects_path)
        try:
            misp_object.generate_attributes(o['data'])
        except NewAttributeError as e:
            log.critical('Error creating attributes, often this is due to custom objects being used. Error: {}'.format(e))
            exit(1)

        # # Add distribution if it has been set
        if o.get('distribution'):
            misp_object.distribution = o.get('distribution')
        # # Add comment to object if it has been set
        if o.get('comment'):
            misp_object.comment = o.get('comment')

        # # Just print the object if --dryrun has been used
        log.info('Processing object: {}'.format(misp_object.to_json()))
        if args.dryrun:
            continue
        else:

            try:
                template_ids = [x['ObjectTemplate']['id'] for x in template if x['ObjectTemplate']['name'] == o['object']]
                if len(template_ids) > 0:
                    template_id = template_ids[0]
                else:
                    raise IndexError
            except IndexError:
                valid_types = ", ".join([x['ObjectTemplate']['name'] for x in template])
                log.critical("Template for type %s not found! Valid types are: %s" % (args.type, valid_types))
                exit(1)

            response = pymisp.add_object(args.event, template_id, misp_object)

            if 'errors' in response.keys():
                log.critical('Error in MISP response! Exiting!')
                log.critical(response['errors'])
                exit(1)

    # print('Event: {}/events/view/{}'.format(secrets.misp_url, response['response']['Event']['id']))