import icalendar
from Acquisition import aq_inner
from datetime import datetime
from plone.uuid.interfaces import IUUID
from zope.interface import implementer
from zope.interface import implements
from zope.publisher.browser import BrowserView
from plone.event.interfaces import IEventAccessor
from plone.event.interfaces import IICalendar
from plone.event.interfaces import IICalendarEventComponent
from plone.event.utils import pydt, utc
from plone.app.event.base import default_timezone
from plone.app.event.base import get_portal_events
from plone.event.utils import tzdel
from Products.ZCatalog.interfaces import ICatalogBrain
import pytz


PRODID = "-//Plone.org//NONSGML plone.app.event//EN"
VERSION = "2.0"


def construct_calendar(context, events):
    """Returns an icalendar.Calendar object.

    :param context: A content object, which is used for calendar details like
                    Title and Description. Usually a container, collection or
                    the event itself.

    :param events: The list of event objects, which are included in this
                   calendar.

    """
    cal = icalendar.Calendar()
    cal.add('prodid', PRODID)
    cal.add('version', VERSION)

    cal_title = context.Title()
    if cal_title: cal.add('x-wr-calname', cal_title)
    cal_desc = context.Description()
    if cal_desc: cal.add('x-wr-caldesc', cal_desc)

    uuid = IUUID(context, None)
    if uuid: # portal object does not have UID
        cal.add('x-wr-relcalid', uuid)

    cal_tz = default_timezone(context)
    if cal_tz: cal.add('x-wr-timezone', cal_tz)

    tzmap = {}
    if not hasattr(events, '__getslice__'): # LazyMap doesn't have __iter__
        events = [events]
    for event in events:
        if ICatalogBrain.providedBy(event):
            event = event.getObject()
        acc = IEventAccessor(event)
        tz = acc.timezone
        # TODO: the standard wants each recurrence to have a valid timezone
        # definition. sounds decent, but not realizable.
        if not acc.whole_day: # whole day events are exported as dates without
                              # timezone information
            tzmap = add_to_zones_map(tzmap, tz, acc.start)
            tzmap = add_to_zones_map(tzmap, tz, acc.end)
        cal.add_component(IICalendarEventComponent(event).to_ical())

    for (tzid, transitions) in tzmap.items():
        cal_tz = icalendar.Timezone()
        cal_tz.add('tzid', tzid)
        cal_tz.add('x-lic-location', tzid)

        for (transition, tzinfo) in transitions.items():

            if tzinfo['dst']:
                cal_tz_sub = icalendar.TimezoneDaylight()
            else:
                cal_tz_sub = icalendar.TimezoneStandard()

            cal_tz_sub.add('tzname', tzinfo['name'])
            cal_tz_sub.add('dtstart', transition)
            cal_tz_sub.add('tzoffsetfrom', tzinfo['tzoffsetfrom'])
            cal_tz_sub.add('tzoffsetto', tzinfo['tzoffsetto'])
            # TODO: add rrule
            #tzi.add('rrule', {'freq': 'yearly', 'bymonth': 10, 'byday': '-1su'})

            cal_tz.add_component(cal_tz_sub)
        cal.add_component(cal_tz)

    return cal


def add_to_zones_map(tzmap, tzid, dt):
    if tzid.lower() == 'utc' or not isinstance(dt, datetime):
        # no need to define UTC nor timezones for date objects.
        return tzmap
    null = datetime(1,1,1)
    tz = pytz.timezone(tzid)
    transitions = getattr(tz, '_utc_transition_times', None)
    if not transitions: return tzmap # we need transition definitions
    dtzl = tzdel(utc(dt))

    # get transition time, which is the dtstart of timezone.
    #     the key function returns the value to compare with. as long as item
    #     is smaller or equal like the dt value in UTC, return the item. as
    #     soon as it becomes greater, compare with the smallest possible
    #     datetime, which wouldn't create a match within the max-function. this
    #     way we get the maximum transition time which is smaller than the
    #     given datetime.
    transition = max(transitions, key=lambda item:item<=dtzl and item or null)

    # get previous transition to calculate tzoffsetfrom
    idx = transitions.index(transition)
    prev_idx = idx > 0 and idx - 1 or idx
    prev_transition = transitions[prev_idx]

    def localize(tz, dt):
        if dt is null: return null # dummy time, edge case
                                   # (dt at beginning of all transitions,
                                   # see above.)
        return pytz.utc.localize(dt).astimezone(tz) # naive to utc and localize
    transition = localize(tz, transition)
    dtstart = tzdel(transition) # timezone dtstart must be in local time
    prev_transition = localize(tz, prev_transition)

    if tzid not in tzmap: tzmap[tzid] = {} # initial
    if dtstart in tzmap[tzid]: return tzmap # already there
    tzmap[tzid][dtstart] = {
            'dst': transition.dst().total_seconds() > 0,
            'name': transition.tzname(),
            'tzoffsetfrom': prev_transition.utcoffset(),
            'tzoffsetto': transition.utcoffset(),
            # TODO: recurrence rule
    }
    return tzmap


@implementer(IICalendar)
def calendar_from_event(context):
    """Event adapter. Returns an icalendar.Calendar object from an Event
    context.

    """
    context = aq_inner(context)
    return construct_calendar(context, context)


@implementer(IICalendar)
def calendar_from_container(context):
    """Container adapter. Returns an icalendar.Calendar object from a
    Containerish context like a Folder.

    """
    context = aq_inner(context)
    path = '/'.join(context.getPhysicalPath())
    result = get_portal_events(context, path=path)
    return construct_calendar(context, result)


@implementer(IICalendar)
def calendar_from_collection(context):
    """Container/Event adapter. Returns an icalendar.Calendar object from a
    Collection.

    """
    context = aq_inner(context)
    result = get_portal_events(context)
    return construct_calendar(context, result)


class ICalendarEventComponent(object):
    """Returns an icalendar object of the event.

    """
    implements(IICalendarEventComponent)

    def __init__(self, context):
        self.context = context
        self.event = IEventAccessor(context)

    def to_ical(self):

        ical = icalendar.Event()

        event = self.event

        # TODO: event.text

        # must be in utc
        ical.add('dtstamp', utc(pydt(datetime.now())))
        ical.add('created', utc(pydt(event.created)))
        ical.add('last-modified', utc(pydt(event.last_modified)))

        ical.add('uid', event.uid)
        ical.add('url', event.url)

        ical.add('summary', event.title)

        if event.description: ical.add('description', event.description)

        if event.whole_day:
            ical.add('dtstart', event.start.date())
            ical.add('dtend', event.end.date())
        else:
            ical.add('dtstart', event.start)
            ical.add('dtend', event.end)

        if event.recurrence:
            for recdef in event.recurrence.split():
                prop, val = recdef.split(':')
                if prop == 'RRULE':
                    ical.add(prop, icalendar.prop.vRecur.from_ical(val))
                elif prop in ('EXDATE', 'RDATE'):
                    factory = icalendar.prop.vDDDLists

                    # localize ex/rdate
                    # TODO: should better already be localized by event object
                    tzid = event.timezone
                    # get list of datetime values from ical string
                    dtlist = factory.from_ical(val, timezone=tzid)

                    ical.add(prop, dtlist)

        if event.location: ical.add('location', event.location)

        # TODO: revisit and implement attendee export according to RFC
        if event.attendees:
            for attendee in event.attendees:
                att = icalendar.prop.vCalAddress(attendee)
                att.params['cn'] = icalendar.prop.vText(attendee)
                att.params['ROLE'] = icalendar.prop.vText('REQ-PARTICIPANT')
                ical.add('attendee', att)

        cn = []
        if event.contact_name:
            cn.append(event.contact_name)
        if event.contact_phone:
            cn.append(event.contact_phone)
        if event.contact_email:
            cn.append(event.contact_email)
        if event.event_url:
            cn.append(event.event_url)
        if cn:
            ical.add('contact', u', '.join(cn))

        if event.subjects:
            for subject in event.subjects:
                ical.add('categories', subject)

        return ical


class EventsICal(BrowserView):
    """Returns events in iCal format.

    """

    def get_ical_string(self):
        cal = IICalendar(self.context)
        return cal.to_ical()

    def __call__(self):
        name = '%s.ics' % self.context.getId()
        self.request.RESPONSE.setHeader('Content-Type', 'text/calendar')
        self.request.RESPONSE.setHeader('Content-Disposition',
            'attachment; filename="%s"' % name)
        self.request.RESPONSE.write(self.get_ical_string())
