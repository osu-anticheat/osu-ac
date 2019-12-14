
import numpy as np
from circleguard.enums import Key, Detect
from circleguard.result import RelaxResult, CorrectionResult, MacroResult
import circleguard.utils as utils
import math

class Investigator:
    """
    Manages the investigation of individual
    :class:`~.replay.Replay`\s for cheats.

    Parameters
    ----------
    replay: :class:`~.replay.Replay`
        The replay to investigate.
    detect: :class:`~.Detect`
        What cheats to investigate the replay for.
    beatmap: :class:`slider.beatmap.Beatmap`
        The beatmap to calculate ur from, with the replay. Should be ``None``
        if ``Detect.RELAX in detect`` is ``False``.

    See Also
    --------
    :class:`~.comparer.Comparer`, for comparing multiple replays.
    """

    MASK = int(Key.M1) | int(Key.M2)

    def __init__(self, replay, detect, beatmap=None):

        self.replay = replay
        # TODO np.array is called on replay.as_list_with_timestamps with object=dtype,
        # and we'll probably want a np array for speed in aim_correction as well.
        # If that object param isn't necessary we can call np.array in init
        self.replay_data = replay.as_list_with_timestamps()
        self.detect = detect
        self.beatmap = beatmap
        self.detect = detect

    def investigate(self):
        d = self.detect
        # TODO we're iterating over the replay three separate times here if
        # all three detects are passed; certainly not the most efficient way
        # to do it. Figure out how to keep the code clean but do all three tests
        # in a single O(n) pass through the replay (or whatever the best case
        # happens to be).
        if Detect.RELAX in d:
            ur = self.ur(self.replay_data, self.beatmap)
            ischeat = ur < d.relax_max_ur
            yield RelaxResult(self.replay, ur, ischeat)
        if Detect.CORRECTION in d:
            snaps = self.aim_correction(self.replay_data, d.correction_max_angle, d.correction_min_distance)
            ischeat = len(snaps) > 1
            yield CorrectionResult(self.replay, snaps, ischeat)
        if Detect.MACRO in d:
            presses = self.macro_detection(self.replay_data, d.macro_max_length)
            ischeat = len(presses) > d.macro_min_count
            yield MacroResult(self.replay, presses, ischeat)

    @staticmethod
    def ur(replay_data, beatmap):
        """
        Calculates the ur of ``replay_data`` when played against ``beatmap``.
        """
        hitobjs = Investigator._parse_beatmap(beatmap)
        keypresses = Investigator._parse_keys(replay_data)
        filtered_array = Investigator._filter_hits(hitobjs, keypresses, beatmap.overall_difficulty)
        diff_array = []

        for hit in filtered_array:
            diff_array.append(hit.hit_error)
        return np.std(diff_array) * 10

    @staticmethod
    def aim_correction(replay_data, max_angle, min_distance):
        """
        Calculates the angle between each set of three points (a,b,c) and finds
        points where this angle is extremely acute neither ``|ab|`` or
        ``|bc|`` are
        small.

        Parameters
        ----------
        replay_data: list[int, float, float, int]
            A list of replay datapoints; [[time, x, y, keys_pressed], ...].
        max_angle: float
            Consider only (a,b,c) where ``∠abc < max_angle``
        min_distance: float
            Consider only (a,b,c) where ``|ab| > min_distance`` and
            ``|ab| > min_distance``.

        Returns
        -------
        list[:class:`~.Snap`]
            Hits where the angle was less than ``max_angle`` and the distance
            was more than ``min_distance``.

        Notes
        -----
        This does not detect correction where multiple datapoints are placed
        at the correction site (which creates a small ``min_distance``).

        Another possible method is to look at the ratio between the angle
        and distance.

        See Also
        --------
        :meth:`~.aim_correction_sam` for an alternative, unused approach
        involving velocity and jerk.
        """
        data = np.array(replay_data).T

        t, xy = data[0][1:-1], data[1:3].T

        # labelling three consecutive points a, b and c
        ab = xy[1:-1] - xy[:-2]
        bc = xy[2:] - xy[1:-1]
        ac = xy[2:] - xy[:-2]
        # Distance a to b, b to c, and a to c
        AB = np.linalg.norm(ab, axis=1)
        BC = np.linalg.norm(bc, axis=1)
        AC = np.linalg.norm(ac, axis=1)
        # Law of cosines, solve for beta
        # AC^2 = AB^2 + BC^2 - 2 * AB * BC * cos(beta)
        # cos(beta) = -(AC^2 - AB^2 - BC^2) / (2*AB*BC)
        num = -(AC ** 2 - AB ** 2 - BC ** 2)
        denom = (2 * AB * BC)
        # use true_divide for handling division by zero
        cos_beta = np.true_divide(num, denom, out=np.full_like(num, np.nan), where=denom!=0)
        # rounding issues makes cos_beta go out of arccos' domain, so restrict it
        cos_beta = np.clip(cos_beta, -1, 1)

        beta = np.rad2deg(np.arccos(cos_beta))

        min_AB_BC = np.minimum(AB, BC)
        dist_mask = min_AB_BC > min_distance
        # use less to avoid comparing to nan
        angl_mask = np.less(beta, max_angle, where=~np.isnan(beta))
        # boolean array of datapoints where both distance and angle requirements are met
        mask = dist_mask & angl_mask

        return [Snap(t, b, d) for (t, b, d) in zip(t[mask], beta[mask], min_AB_BC[mask])]

    @staticmethod
    def aim_correction_sam(replay_data, num_jerks, min_jerk):
        """
        Calculates the jerk at each moment in the Replay, counts the number of times
        it exceeds min_jerk and reports a positive if that number is over num_jerks.
        Also reports all suspicious jerks and their timestamps.

        WARNING
        -------
        Unused function. Kept for historical purposes and ease of viewing in
        case we want to switch to this track of aim correction in the future,
        or provide it as an alternative.
        """

        # get all replay data as an array of type [(t, x, y, k)]
        txyk = np.array(replay_data)

        # drop keypresses
        txy = txyk[:, :3]

        # separate time and space
        t = txy[:, 0]
        xy = txy[:, 1:]

        # j_x = (d/dt)^3 x
        # calculated as (d/dT dT/dt)^3 x = (dT/dt)^3 (d/dT)^3 x
        # (d/dT)^3 x = d(d(dx/dT)/dT)/dT
        # (dT/dt)^3 = 1/(dt/dT)^3
        dtdT = np.diff(t)
        d3xy = np.diff(xy, axis=0, n=3)
        # safely calculate the division and replace with zero if the divisor is zero
        # dtdT is sliced with 2: because differentiating drops one element for each order (slice (n - 1,) to (n - 3,))
        # d3xy is of shape (n - 3, 2) so dtdT is also reshaped from (n - 3,) to (n - 3, 1) to align the axes.
        jerk = np.divide(d3xy, dtdT[2:, None] ** 3, out=np.zeros_like(d3xy), where=dtdT[2:,None]!=0)

        # take the absolute value of the jerk
        jerk = np.linalg.norm(jerk, axis=1)

        # create a mask of where the jerk reaches suspicious values
        anomalous = jerk > min_jerk
        # and retrieve and store the timestamps and the values themself
        timestamps = t[3:][anomalous]
        values = jerk[anomalous]
        # reshape to an array of type [(t, j)]
        jerks = np.vstack((timestamps, values)).T

        # count the anomalies
        ischeat = anomalous.sum() > num_jerks

        return [jerks, ischeat]

    @staticmethod
    def macro_detection(replay_data, max_length):
        """
        Returns a list of :meth:`~.Press`\s that have a longer ``press_length`` than ``max_length``.

        Parameters
        ----------
        replay_data: list[int, float, float, int]
            A list of replay datapoints; [[time, x, y, keys_pressed], ...].
        max_length: int
            Amount of time needed to classify a press as cheated.

        Returns
        -------
        list[:class:`~.Press`]
            A list of Presses.
        """
        keypresses = Investigator._parse_keys(replay_data)
        presses = [p for p in keypresses if p.press_length < max_length]
        return presses

    @staticmethod
    def _parse_beatmap(beatmap):
        """
        Parses the beatmap.

        Parameters
        ----------
        beatmap: :class:`slider.beatmap.Beatmap`
            The beatmap from which to extract the hit objects from.

        Returns
        -------
        list[:class:`~.HitObject`]
            A list of beatmap hitobjects.
        """
        hitobjs = []

        # parse hitobj
        for hit in beatmap.hit_objects_no_spinners:
            hitobjs.append(HitObject(hit))
        return hitobjs

    @staticmethod
    def _parse_keys(data):
        """
        Parses the raw replay data into :class:`~.Press`\es.

        Parameters
        ----------
        data: list[int, float, float, int]
            A list of replay datapoints; [[time, x, y, keys_pressed], ...].

        Returns
        -------
        list[:class:`~.Press`]
            A list of Presses.
        """
        data = np.array(data, dtype=object)
        presses = []
        buffer_k1 = None
        buffer_k2 = None
        for i in data:
            if Key.M1 in Key(i[3]) and buffer_k1 is None:
                d = i
                d[3] = int(Key(d[3]) & Key.K1 | Key(d[3]) & Key.M1)
                buffer_k1 = d
            elif Key.M1 not in Key(i[3]) and buffer_k1 is not None:
                presses.append(Press(buffer_k1, i))
                buffer_k1 = None

            if Key.M2 in Key(i[3]) and buffer_k2 is None:
                d = i
                d[3] = int(Key(d[3]) & Key.K2 | Key(d[3]) & Key.M2)
                buffer_k2 = d
            elif Key.M2 not in Key(i[3]) and buffer_k2 is not None:
                presses.append(Press(buffer_k2, i))
                buffer_k2 = None
        return np.array(presses)

    @staticmethod
    def hit_map(replay_data, beatmap):
        """
        Generates a list of :class:`Hit`\s.

        Parameters
        ----------
        replay_data: list[int, float, float, int]
            A list of replay datapoints; [[time, x, y, keys_pressed], ...].
        beatmap: :class:`slider.beatmap.Beatmap`
            The beatmap to which the Presses are mapped to.

        Returns
        -------
        list[:class:`~.Hit`]
            A list of :class:`Hit`\s

        """
        hitobjs = Investigator._parse_beatmap(beatmap)
        keypresses = Investigator._parse_keys(replay_data)
        hit_array = Investigator._filter_hits(hitobjs, keypresses, beatmap.overall_difficulty)
        return hit_array

    @staticmethod
    def _filter_hits(hitobjs, keypresses, OD):
        """
        Maps ``hitobjs`` onto ``keypresses``, removing all useless ``keypresses`` in the process.
        This class is expected to be used with the output of :meth:`~._parse_beatmap()` and :meth:`~._parse_keys`

        Parameters
        ----------
        hitobjs: list[:class:`~.HitObject`]
            A list of beatmap hitobjects. Usually the direct output of :meth:`~._parse_beatmap()`
        keypresses: list[:class:`~.Press`]
            A list of keypress events. Usually the direct output of :meth:`~._parse_keys`
        OD: float
            The Overall Difficulty to calculate the hitwindow from.

        Returns
        -------
        list[:class:`~.Hit`]
            A list of hit events.
        """
        array = []
        hitwindow = 150 + 50 * (5 - OD) / 5

        object_i = 0
        press_i = 0

        while object_i < len(hitobjs) and press_i < len(keypresses):
            hitobj = hitobjs[object_i]
            press = keypresses[press_i]

            if press.time_press < hitobj.time - hitwindow / 2:
                press_i += 1
            elif press.time_press > hitobj.time + hitwindow / 2:
                object_i += 1
            else:
                array.append(Hit(hitobj, press))
                press_i += 1
                object_i += 1

        return array


class Snap:
    """
    A suspicious hit in a replay, specifically so because it snaps away from
    the otherwise normal path. Snaps currently represent the middle datapoint
    in a set of three replay datapoints.

    Parameters
    ----------
    time: int
        The time value of the middle datapoint, in ms. 0 represents the
        beginning of the replay.
    angle: float
        The angle between the three datapoints.
    distance: float
        ``min(dist_a_b, dist_b_c)`` if ``a``, ``b``, and ``c`` are three
        datapoints with ``b`` being the middle one.

    See Also
    --------
    :meth:`~.Investigator.aim_correction`
    """
    def __init__(self, time, angle, distance):
        self.time = time
        self.angle = angle
        self.distance = distance


class Press:
    """
    Represents a press in a replay.

    Parameters
    ----------
    hit_begin: list[int, float, float, int]
        A replay datapoint; [time, x, y, keys_pressed]. This is the beginning of the Press.
    hit_end: list[int, float, float, int]
        A replay datapoint; [time, x, y, keys_pressed]. This is the end of the Press.

    Attributes
    ----------
    x : float
        The X coordinate from hit_begin.
    y : float
        The Y coordinate from hit_begin.
    time_press: int
        The the time from hit_begin.
    time_release: int
        The the time from hit_release.
    press_length: int
        Amount of time between hit_begin and hit_end
    key: :class:`enums.Key`
        The Key which was used to press. Calculated by subtracting hit_end from the hit_begin
    """
    def __init__(self, hit_begin, hit_end):
        self.x = hit_begin[1]
        self.y = hit_begin[2]
        self.time_press = hit_begin[0]
        self.time_release = hit_end[0]
        self.press_length = hit_end[0] - hit_begin[0]
        self.key = Key(hit_begin[3] - hit_end[3])


class HitObject:
    """
    Represents a HitObject of a beatmap.

    Parameters
    ----------
    hitobj: :class:`slider.beatmap.Circle`, :class:`slider.beatmap.Slider`
        A HitObject of the beatmap.
    Attributes
    ----------
    x : int
        The X coordinate of the Hitobject.
    y : int
        The Y coordinate of the Hitobject.
    time: float
        The the time of the Hitobject.
    """
    def __init__(self, hitobj):
        self.x = hitobj.position.x
        self.y = hitobj.position.y
        self.time = hitobj.time.total_seconds() * 1000


class Hit:
    """
    Represents a hit of a Hitobject in a replay.

    Parameters
    ----------
    hitobj: :class:`HitObject`
        A HitObject.
    press: :class:`Press`
        A Press.

    Attributes
    ----------
    press: :class:`Press`
        The passed press.
    hitobject: :class:`HitObject`
        The passed hitobj.
    dx: float
        The Δ of the X coordinate from the Press and the HitObject.
    dy: float
        The Δ of the Y coordinate from the Press and the HitObject.
    hit_error: float
        The Δ of the time from the Press and the HitObject.
    """
    def __init__(self, hitobj, press):
        self.press = press
        self.hitobject = hitobj
        self.dx = hitobj.x - press.x
        self.dy = hitobj.y - press.y
        self.hit_error = hitobj.time - press.time_press
