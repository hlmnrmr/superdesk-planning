# -*- coding: utf-8; -*-
#
# This file is part of Superdesk.
#
# Copyright 2013, 2014 Sourcefabric z.u. and contributors.
#
# For the full copyright and license information, please see the
# AUTHORS and LICENSE files distributed with this source code, or
# at https://www.sourcefabric.org/superdesk/license

"""Superdesk Events"""

import superdesk
import logging
from superdesk import get_resource_service
from superdesk.resource import not_analyzed
from superdesk.errors import SuperdeskApiError
from superdesk.metadata.utils import generate_guid
from superdesk.metadata.item import GUID_NEWSML, ITEM_TYPE, metadata_schema
from superdesk.notification import push_notification
from apps.archive.common import set_original_creator, get_user
from superdesk.users.services import current_user_has_privilege
from superdesk.utc import utcnow
from .common import UPDATE_SINGLE, UPDATE_FUTURE, UPDATE_ALL, UPDATE_METHODS, \
    get_max_recurrent_events, WORKFLOW_STATE_SCHEMA, PUBLISHED_STATE_SCHEMA
from dateutil.rrule import rrule, YEARLY, MONTHLY, WEEKLY, DAILY, MO, TU, WE, TH, FR, SA, SU
from eve.defaults import resolve_default_values
from eve.methods.common import resolve_document_etag
from eve.utils import config, ParsedRequest
from flask import current_app as app, json
import itertools
import copy
import pytz
import re
from deepdiff import DeepDiff
from copy import deepcopy

logger = logging.getLogger(__name__)

FREQUENCIES = {'DAILY': DAILY, 'WEEKLY': WEEKLY, 'MONTHLY': MONTHLY, 'YEARLY': YEARLY}
DAYS = {'MO': MO, 'TU': TU, 'WE': WE, 'TH': TH, 'FR': FR, 'SA': SA, 'SU': SU}

organizer_roles = {
    'eorol:artAgent': 'Artistic agent',
    'eorol:general': 'General organiser',
    'eorol:tech': 'Technical organiser',
    'eorol:travAgent': 'Travel agent',
    'eorol:venue': 'Venue organiser'
}


class EventsService(superdesk.Service):
    """Service class for the events model."""

    def post_in_mongo(self, docs, **kwargs):
        for doc in docs:
            resolve_default_values(doc, app.config['DOMAIN'][self.datasource]['defaults'])
        self.on_create(docs)
        resolve_document_etag(docs, self.datasource)
        ids = self.backend.create_in_mongo(self.datasource, docs, **kwargs)
        self.on_created(docs)
        return ids

    def patch_in_mongo(self, id, document, original):
        res = self.backend.update_in_mongo(self.datasource, id, document, original)
        return res

    def on_fetched(self, docs):
        for doc in docs['_items']:
            self._set_has_planning_flag(doc)

    def on_fetched_item(self, doc):
        self._set_has_planning_flag(doc)

    def _set_has_planning_flag(self, doc):
        doc['has_planning'] = self.has_planning_items(doc)

    def has_planning_items(self, doc):
        plannings = list(get_resource_service('planning').find(where={
            'event_item': doc[config.ID_FIELD]
        }))
        return len(plannings) > 0

    def set_ingest_provider_sequence(self, item, provider):
        """Sets the value of ingest_provider_sequence in item.

        :param item: object to which ingest_provider_sequence to be set
        :param provider: ingest_provider object, used to build the key name of sequence
        """
        sequence_number = get_resource_service('sequences').get_next_sequence_number(
            key_name='ingest_providers_{_id}'.format(_id=provider[config.ID_FIELD]),
            max_seq_number=app.config['MAX_VALUE_OF_INGEST_SEQUENCE']
        )
        item['ingest_provider_sequence'] = str(sequence_number)

    def on_create(self, docs):
        # events generated by recurring rules
        generated_events = []
        for event in docs:
            # generates an unique id
            if 'guid' not in event:
                event['guid'] = generate_guid(type=GUID_NEWSML)
            event['_id'] = event['guid']
            # set the author
            set_original_creator(event)

            # overwrite expiry date
            overwrite_event_expiry_date(event)

            # We ignore the 'update_method' on create
            if 'update_method' in event:
                del event['update_method']

            # generates events based on recurring rules
            if event['dates'].get('recurring_rule', None):
                generated_events.extend(generate_recurring_events(event))
                # remove the event that contains the recurring rule. We don't need it anymore
                docs.remove(event)
        if generated_events:
            docs.extend(generated_events)

    def on_created(self, docs):
        """Send WebSocket Notifications for created Events

        Generate the list of IDs for recurring and non-recurring events
        Then send this list off to the clients so they can fetch these events
        """
        notifications_sent = []

        for doc in docs:
            event_type = 'events:created'
            event_id = str(doc.get(config.ID_FIELD))
            user_id = str(doc.get('original_creator', ''))

            if doc.get('recurrence_id'):
                event_type = 'events:created:recurring'
                event_id = str(doc['recurrence_id'])

            # Don't send notification if one has already been sent
            # This is to ensure recurring events doesn't send multiple notifications
            if event_id in notifications_sent or 'previous_recurrence_id' in doc:
                continue

            notifications_sent.append(event_id)
            push_notification(
                event_type,
                item=event_id,
                user=user_id
            )

    def can_edit(self, item, user_id):
        # Check privileges
        if not current_user_has_privilege('planning_event_management'):
            return False, 'User does not have sufficient permissions.'
        return True, ''

    def update(self, id, updates, original):
        item = self.backend.update(self.datasource, id, updates, original)
        return item

    def publish(self, resource, id, updates, original):
        pass

    def on_update(self, updates, original):
        """Update single or series of recurring events.

        Determine if the supplied event is a single event or a
        series of recurring events, and call the appropriate method
        for the event type.
        """
        if 'skip_on_update' in updates:
            # this is a recursive update (see below)
            del updates['skip_on_update']
            return

        if 'update_method' in updates:
            update_method = updates['update_method']
            del updates['update_method']
        else:
            update_method = UPDATE_SINGLE

        user = get_user()
        user_id = user.get(config.ID_FIELD) if user else None

        if user_id:
            updates['version_creator'] = user_id

        lock_user = original.get('lock_user', None)
        str_user_id = str(user.get(config.ID_FIELD)) if user_id else None

        if lock_user and str(lock_user) != str_user_id:
            raise SuperdeskApiError.forbiddenError('The item was locked by another user')

        # Run the specific methods based on if the original is a
        # single or a series of recurring events
        if not original.get('dates', {}).get('recurring_rule', None):
            self._update_single_event(updates, original)
        else:
            self._update_recurring_events(updates, original, update_method)

    def _update_single_event(self, updates, original):
        """Updates the metadata and occurrence of a single event.

        If recurring_rule is provided, we convert this single event into
        a series of recurring events, otherwise we simply update this event.
        """

        # Determine if we're to convert this single event to a recurring series of events
        if updates.get('dates', {}).get('recurring_rule', None) is not None:
            generated_events = self._convert_to_recurring_event(updates, original)

            push_notification(
                'events:updated:recurring',
                item=str(original[config.ID_FIELD]),
                user=str(updates.get('version_creator', '')),
                recurrence_id=str(generated_events[0]['recurrence_id'])
            )
        else:
            push_notification(
                'events:updated',
                item=str(original[config.ID_FIELD]),
                user=str(updates.get('version_creator', ''))
            )

    def _update_recurring_events(self, updates, original, update_method):
        """Method to update recurring events.

        If the recurring_rule has been removed for this event, process
        it separately, otherwise update the event and/or its recurring rules
        """
        # If dates has not been updated, then we're updating the metadata
        # of this series of recurring events
        if not updates.get('dates'):
            self._update_metadata_recurring(updates, original, update_method)
            # And finally push a notification to connected clients
            push_notification(
                'events:updated:recurring',
                item=str(original[config.ID_FIELD]),
                recurrence_id=str(original['recurrence_id']),
                user=str(updates.get('version_creator', ''))
            )
            return
        # Otherwise if the recurring_rule has bee removed, then we're
        # disassociating this event from the series of recurring events
        elif not updates['dates'].get('recurring_rule', None):
            # Recurring rule has been removed for this event,
            # Remove this rule and return from this method
            self._remove_recurring_rules(updates, original)
            push_notification(
                'events:updated',
                item=str(original[config.ID_FIELD]),
                user=str(updates.get('version_creator'))
            )
            return
        elif update_method == UPDATE_SINGLE:
            set_next_occurrence(updates)

            push_notification(
                'events:updated:recurring',
                item=str(original[config.ID_FIELD]),
                recurrence_id=str(original['recurrence_id']),
                user=str(updates.get('version_creator', ''))
            )
            return
        # Otherwise we're modifying the recurring_rules for the event
        elif update_method in [UPDATE_FUTURE, UPDATE_ALL]:
            self._update_recurring_rules(updates, original, update_method)

            # And finally push a notification to connected clients
            push_notification(
                'events:updated:recurring',
                item=str(original[config.ID_FIELD]),
                recurrence_id=str(updates.get('recurrence_id', original['recurrence_id'])),
                previous_recurrence_id=str(updates.get('previous_recurrence_id', None)),
                user=str(updates.get('version_creator', ''))
            )

    def _update_metadata_recurring(self, updates, original, update_method):
        """Update the Metadata for a series of recurring events

        Based on the update_method, it will update:
        single: the provided event only
        future: the provided event, and all future events
        all: all events in the series
        """
        events = []
        if update_method == UPDATE_FUTURE:
            historic, past, future = self.get_recurring_timeline(original)
            events.extend(future)
        elif update_method == UPDATE_ALL:
            historic, past, future = self.get_recurring_timeline(original)
            events.extend(historic)
            events.extend(past)
            events.extend(future)

        for e in events:
            self.patch(e[config.ID_FIELD], updates)
            app.on_updated_events(updates, {'_id': e[config.ID_FIELD]})

    def _patch_event_in_recurrent_series(self, event_id, updated_event):
        updated_event['skip_on_update'] = True
        self.patch(event_id, updated_event)
        app.on_updated_events(updated_event, {'_id': event_id})

    def _get_empty_updates_for_recurring_event(self, event):
        updates = {}
        updates['dates'] = copy.deepcopy(event['dates'])
        return updates

    def _remove_recurring_rules(self, updates, original):
        """Remove recurring rules for an event
        """

        (historic, past, future) = self.get_recurring_timeline(original)

        # 1 - Disassociate the selected event from the series
        updates['recurrence_id'] = None

        # 2 - Original series will end one event before the selected event
        self._set_series_end_date(historic + past)

        # 3 - Create a new series for the future events
        count = future[0]['dates']['recurring_rule']['count'] - (len(historic) + len(past) + 1)
        self._set_series_end_count(future, generate_guid(type=GUID_NEWSML), count)

    def _convert_to_recurring_event(self, updates, original):
        """Convert a single event to a series of recurring events"""
        # Convert the single event to a series of recurring events
        updates['recurrence_id'] = generate_guid(type=GUID_NEWSML)

        merged = copy.deepcopy(original)
        merged.update(updates)
        generated_events = generate_recurring_events(merged)

        # Remove the first element in the list (the current event being updated)
        # And update the start/end dates to be in line with the new recurring rules
        updated_event = generated_events.pop(0)
        updates['dates']['start'] = updated_event['dates']['start']
        updates['dates']['end'] = updated_event['dates']['end']

        # Create the new events and generate their history
        self.create(generated_events)
        app.on_inserted_events(generated_events)
        return generated_events

    def _update_recurring_rules(self, updates, original, update_method):
        """Modify the recurring rules of a series of recurring events

        This is achieved by splitting the series into 2 separate series of events
        based on the updated_method ('future' or 'all').
        If existing events do not occur in the new recurring_rules, then either
        delete them (or spike if they have Planning items, or a set 'pubstatus').
        """
        historic, past, future = self.get_recurring_timeline(original)

        # Determine if the selected event is the first one, if so then
        # act as if we're changing future events
        if len(historic) == 0 and len(past) == 0:
            update_method = UPDATE_FUTURE

        if update_method == UPDATE_FUTURE:
            new_series = [updates] + future
        else:
            new_series = past + [updates] + future

        # Check if the updates contain only an update in event's time
        update_time_only, new_start_time, new_end_time = self._is_only_time_updated(
            original.get('dates'), updates.get('dates'))

        # Updating the recurring rules is only allowed via the `events_reschedule` endpoint
        # So bail out here
        if not update_time_only:
            return

        self._set_series_time(new_series, new_start_time, new_end_time)

    def _set_series_time(self, series, new_start_time, new_end_time):
        # Update the time for all event in the series
        for event in series:
            if event.get(config.ID_FIELD):
                time_updates = self._get_empty_updates_for_recurring_event(event)
                if new_start_time:
                    time_updates['dates']['start'] = time_updates.get('dates').get('start').replace(
                        hour=new_start_time.hour,
                        minute=new_start_time.minute)

                if new_end_time:
                    time_updates['dates']['end'] = time_updates.get('dates').get('end').replace(
                        hour=new_end_time.hour,
                        minute=new_end_time.minute)

                self._patch_event_in_recurrent_series(event[config.ID_FIELD], time_updates)

    def _set_series_end_date(self, series):
        for event in series:
            updates = self._get_empty_updates_for_recurring_event(event)
            recurring_rule = updates['dates']['recurring_rule']
            if recurring_rule:
                recurring_rule['until'] = series[-1]['dates']['start']
                recurring_rule['endRepeatMode'] = 'until'
                recurring_rule['count'] = None

                self._patch_event_in_recurrent_series(event[config.ID_FIELD], updates)

    def _set_series_end_count(self, series, new_recurrence_id, count):
        for event in series:
            updates = self._get_empty_updates_for_recurring_event(event)
            updates['previous_recurrence_id'] = updates.get('recurrence_id', None)
            updates['recurrence_id'] = new_recurrence_id
            recurring_rule = updates['dates']['recurring_rule']
            if recurring_rule and recurring_rule['endRepeatMode'] == 'count':
                recurring_rule['count'] = count

            self._patch_event_in_recurrent_series(event[config.ID_FIELD], updates)

    def _filter_events_with_planning_items(self, events):
        planning_service = get_resource_service('planning')

        event_ids = [event[config.ID_FIELD] for event in events]

        planning_items = list(planning_service.get_from_mongo(
            req=None, lookup={'event_item': {'$in': event_ids}}
        ))

        return set([planning['event_item'] for planning in planning_items])

    def get_recurring_timeline(self, selected):
        """Utility method to get all events in the series

        This splits up the series of events into 3 separate arrays.
        Historic: event.dates.start < utcnow()
        Past: utcnow() < event.dates.start < selected.dates.start
        Future: event.dates.start > selected.dates.start
        """
        historic = []
        past = []
        future = []

        selected_start = selected.get('dates', {}).get('start', utcnow())

        req = ParsedRequest()
        req.sort = '[("dates.start", 1)]'
        req.where = json.dumps({
            '$and': [
                {'recurrence_id': selected['recurrence_id']},
                {'_id': {'$ne': selected[config.ID_FIELD]}}
            ]
        })

        for event in list(self.get_from_mongo(req, {})):
            end = event['dates']['end']
            start = event['dates']['start']
            if end < utcnow():
                historic.append(event)
            elif start < selected_start:
                past.append(event)
            elif start > selected_start:
                future.append(event)

        return historic, past, future

    def _is_only_time_updated(self, original_dates, updated_dates):
        new_start_time = None
        new_end_time = None

        diffs = DeepDiff(original_dates, updated_dates, ignore_order=True)
        values_changed = diffs.get('values_changed')
        if values_changed:
            for diff in values_changed:
                if diff != 'root[\'start\']' and diff != 'root[\'end\']':
                    # something other than start/end has changed
                    return False, new_start_time, new_end_time
                elif values_changed.get(diff).get('new_value').date() != \
                        values_changed.get(diff).get('old_value').date():
                    # date has changed, not just time
                    return False, new_start_time, new_end_time
                else:
                    if diff == 'root[\'start\']':
                        new_start_time = values_changed.get(diff).get('new_value').time()
                    else:
                        new_end_time = values_changed.get(diff).get('new_value').time()

        return True, new_start_time, new_end_time


event_type = deepcopy(superdesk.Resource.rel('events', type='string'))
event_type['mapping'] = not_analyzed

events_schema = {
    # Identifiers
    '_id': metadata_schema['_id'],
    'guid': metadata_schema['guid'],
    'unique_id': metadata_schema['unique_id'],
    'unique_name': metadata_schema['unique_name'],
    'version': metadata_schema['version'],
    'ingest_id': metadata_schema['ingest_id'],
    'recurrence_id': {
        'type': 'string',
        'mapping': not_analyzed,
        'nullable': True,
    },

    # This is used when recurring series are split
    'previous_recurrence_id': {
        'type': 'string',
        'mapping': not_analyzed,
        'nullable': True
    },

    # Audit Information
    'original_creator': metadata_schema['original_creator'],
    'version_creator': metadata_schema['version_creator'],
    'firstcreated': metadata_schema['firstcreated'],
    'versioncreated': metadata_schema['versioncreated'],

    # Ingest Details
    'ingest_provider': metadata_schema['ingest_provider'],
    'source': metadata_schema['source'],
    'original_source': metadata_schema['original_source'],
    'ingest_provider_sequence': metadata_schema['ingest_provider_sequence'],

    'event_created': {
        'type': 'datetime'
    },
    'event_lastmodified': {
        'type': 'datetime'
    },
    # Event Details
    # NewsML-G2 Event properties See IPTC-G2-Implementation_Guide 15.2
    'name': {
        'type': 'string',
        'required': True,
    },
    'definition_short': {'type': 'string'},
    'definition_long': {'type': 'string'},
    'internal_note': {'type': 'string'},
    'anpa_category': metadata_schema['anpa_category'],
    'files': {
        'type': 'list',
        'nullable': True,
        'schema': superdesk.Resource.rel('events_files'),
        'mapping': not_analyzed,
    },
    'relationships': {
        'type': 'dict',
        'schema': {
            'broader': {'type': 'string'},
            'narrower': {'type': 'string'},
            'related': {'type': 'string'}
        },
    },
    'links': {
        'type': 'list',
        'nullable': True
    },

    # NewsML-G2 Event properties See IPTC-G2-Implementation_Guide 15.4.3
    'dates': {
        'type': 'dict',
        'schema': {
            'start': {'type': 'datetime'},
            'end': {'type': 'datetime'},
            'tz': {'type': 'string'},
            'duration': {'type': 'string'},
            'confirmation': {'type': 'string'},
            'recurring_date': {
                'type': 'list',
                'nullable': True,
                'mapping': {
                    'type': 'date'
                }
            },
            'recurring_rule': {
                'type': 'dict',
                'schema': {
                    'frequency': {'type': 'string'},
                    'interval': {'type': 'integer'},
                    'endRepeatMode': {
                        'type': 'string',
                        'allowed': ['count', 'until']
                    },
                    'until': {'type': 'datetime', 'nullable': True},
                    'count': {'type': 'integer', 'nullable': True},
                    'bymonth': {'type': 'string', 'nullable': True},
                    'byday': {'type': 'string', 'nullable': True},
                    'byhour': {'type': 'string', 'nullable': True},
                    'byminute': {'type': 'string', 'nullable': True},
                },
                'nullable': True
            },
            'occur_status': {
                'nullable': True,
                'type': 'dict',
                'mapping': {
                    'properties': {
                        'qcode': not_analyzed,
                        'name': not_analyzed
                    }
                },
                'schema': {
                    'qcode': {'type': 'string'},
                    'name': {'type': 'string'},
                }
            },
            'ex_date': {
                'type': 'list',
                'mapping': {
                    'type': 'date'
                }
            },
            'ex_rule': {
                'type': 'dict',
                'schema': {
                    'frequency': {'type': 'string'},
                    'interval': {'type': 'string'},
                    'until': {'type': 'datetime', 'nullable': True},
                    'count': {'type': 'integer', 'nullable': True},
                    'bymonth': {'type': 'string', 'nullable': True},
                    'byday': {'type': 'string', 'nullable': True},
                    'byhour': {'type': 'string', 'nullable': True},
                    'byminute': {'type': 'string', 'nullable': True}
                }
            }
        }
    },  # end dates
    'occur_status': {
        'type': 'dict',
        'schema': {
            'qcode': {'type': 'string'},
            'name': {'type': 'string'},
            'label': {'type': 'string'}
        }
    },
    'news_coverage_status': {
        'type': 'dict',
        'schema': {
            'qcode': {'type': 'string'},
            'name': {'type': 'string'}
        }
    },
    'registration': {
        'type': 'string'
    },
    'access_status': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },

    # Content metadata
    'subject': metadata_schema['subject'],
    'slugline': metadata_schema['slugline'],

    # Item metadata
    'location': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': {'type': 'string'},
                'name': {'type': 'string'},
                'geo': {'type': 'string'},
                'type': {'type': 'string'},
                'location': {'type': 'geo_point'},
            }
        }
    },
    'participant': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },
    'participant_requirement': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },
    'organizer': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },
    'event_contact_info': {
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },
    'event_language': {  # TODO: this is only placeholder schema
        'type': 'list',
        'mapping': {
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed
            }
        }
    },

    # These next two are for spiking/unspiking and purging events
    'state': WORKFLOW_STATE_SCHEMA,
    'expiry': {
        'type': 'datetime',
        'nullable': True
    },
    # says if the event is for internal usage or published
    'pubstatus': PUBLISHED_STATE_SCHEMA,

    'lock_user': metadata_schema['lock_user'],
    'lock_time': metadata_schema['lock_time'],
    'lock_session': metadata_schema['lock_session'],
    'lock_action': metadata_schema['lock_action'],

    # The update method used for recurring events
    'update_method': {
        'type': 'string',
        'allowed': UPDATE_METHODS,
        'mapping': not_analyzed,
        'nullable': True
    },

    # Item type used by superdesk publishing
    ITEM_TYPE: {
        'type': 'string',
        'mapping': not_analyzed,
        'default': 'event',
    },

    # Named Calendars
    'calendars': {
        'type': 'list',
        'nullable': True,
        'mapping': {
            'type': 'object',
            'properties': {
                'qcode': not_analyzed,
                'name': not_analyzed,
            }
        }
    },

    # The previous state the item was in before for example being spiked,
    # when un-spiked it will revert to this state
    'revert_state': metadata_schema['revert_state'],

    # Used when duplicating/rescheduling of Events
    'duplicate_from': event_type,
    'duplicate_to': {
        'type': 'list',
        'nullable': True,
        'schema': superdesk.Resource.rel('events', type='string'),
        'mapping': not_analyzed
    }
}  # end events_schema


class EventsResource(superdesk.Resource):
    """Resource for events data model

    See IPTC-G2-Implementation_Guide (version 2.21) Section 15.4 for schema details
    """

    url = 'events'
    schema = events_schema
    item_url = 'regex("[\w,.:-]+")'
    resource_methods = ['GET', 'POST']
    datasource = {
        'source': 'events',
        'search_backend': 'elastic',
        'default_sort': [('dates.start', 1)],
    }
    item_methods = ['GET', 'PATCH', 'PUT']
    public_methods = ['GET']
    privileges = {'POST': 'planning_event_management',
                  'PATCH': 'planning_event_management'}


def generate_recurring_dates(start, frequency, interval=1, endRepeatMode='count',
                             until=None, byday=None, count=5, tz=None):
    """

    Returns list of dates related to recurring rules

    :param start datetime: date when to start
    :param frequency str: DAILY, WEEKLY, MONTHLY, YEARLY
    :param interval int: indicates how often the rule repeats as a positive integer
    :param until datetime: date after which the recurrence rule expires
    :param byday str or list: "MO TU"
    :param count int: number of occurrences of the rule
    :return list: list of datetime

    """
    # if tz is given, respect the timzone by starting from the local time
    # NOTE: rrule uses only naive datetime
    if tz:
        try:
            # start can already be localized
            start = pytz.UTC.localize(start)
        except ValueError:
            pass
        start = start.astimezone(tz).replace(tzinfo=None)
        if until:
            until = until.astimezone(tz).replace(tzinfo=None)

    if frequency == 'DAILY':
        byday = None

    # check format of the recurring_rule byday value
    if byday and re.match(r'^-?[1-5]+.*', byday):
        # byday uses monthly or yearly frequency rule with day of week and
        # preceeding day of month intenger byday value
        # examples:
        # 1FR - first friday of the month
        # -2MON - second to last monday of the month
        if byday[:1] == '-':
            day_of_month = int(byday[:2])
            day_of_week = byday[2:]
        else:
            day_of_month = int(byday[:1])
            day_of_week = byday[1:]

        byweekday = DAYS.get(day_of_week)(day_of_month)
    else:
        # byday uses DAYS constants
        byweekday = byday and [DAYS.get(d) for d in byday.split()] or None
    # TODO: use dateutil.rrule.rruleset to incude ex_date and ex_rule
    dates = rrule(
        FREQUENCIES.get(frequency),
        dtstart=start,
        until=until,
        byweekday=byweekday,
        count=count,
        interval=interval,
    )
    # if a timezone has been applied, returns UTC
    if tz:
        return (tz.localize(dt).astimezone(pytz.UTC).replace(tzinfo=None) for dt in dates)
    else:
        return (date for date in dates)


def setRecurringMode(event):
    endRepeatMode = event.get('dates', {}).get('recurring_rule', {}).get('endRepeatMode')
    if endRepeatMode == 'count':
        event['dates']['recurring_rule']['until'] = None
    elif endRepeatMode == 'until':
        event['dates']['recurring_rule']['count'] = None


def overwrite_event_expiry_date(event):
    if 'expiry' in event:
        event['expiry'] = event['dates']['end']


def generate_recurring_events(event):
    generated_events = []
    setRecurringMode(event)

    # Get the recurrence_id, or generate one if it doesn't exist
    recurrence_id = event.get('recurrence_id', generate_guid(type=GUID_NEWSML))

    # compute the difference between start and end in the original event
    time_delta = event['dates']['end'] - event['dates']['start']
    # for all the dates based on the recurring rules:
    for date in itertools.islice(generate_recurring_dates(
            start=event['dates']['start'],
            tz=event['dates'].get('tz') and pytz.timezone(event['dates']['tz'] or None),
            **event['dates']['recurring_rule']
    ), 0, get_max_recurrent_events()):  # set a limit to prevent too many events to be created
        # create event with the new dates
        new_event = copy.deepcopy(event)

        # Remove fields not required by the new events
        for key in list(new_event.keys()):
            if key.startswith('_'):
                new_event.pop(key)
            elif key.startswith('lock_'):
                new_event.pop(key)

        new_event['dates']['start'] = date
        new_event['dates']['end'] = date + time_delta
        # set a unique guid
        new_event['guid'] = generate_guid(type=GUID_NEWSML)
        new_event['_id'] = new_event['guid']
        # set the recurrence id
        new_event['recurrence_id'] = recurrence_id

        # set expiry date
        overwrite_event_expiry_date(new_event)

        generated_events.append(new_event)

    return generated_events


def set_next_occurrence(updates):
    new_dates = [date for date in itertools.islice(generate_recurring_dates(
        start=updates['dates']['start'],
        tz=updates['dates'].get('tz') and pytz.timezone(updates['dates']['tz'] or None),
        **updates['dates']['recurring_rule']), 0, 10)]
    time_delta = updates['dates']['end'] - updates['dates']['start']
    updates['dates']['start'] = new_dates[0]
    updates['dates']['end'] = new_dates[0] + time_delta
