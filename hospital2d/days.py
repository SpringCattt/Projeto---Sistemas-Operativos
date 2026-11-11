# days.py - part of the Hospital (sim.py): The day and the money: wages by the hour, closing time, the last call, the day's report, payments and the staff bonus credit.
from collections import Counter
import config


class DayMixin:
    """The day and the money: wages by the hour, closing time, the last call, the day's report, payments and the staff bonus credit."""

    def run_day(self):
        """The day's own thread: pays the staff by the hour, closes the doors at midnight, and ends the day when the
        last patient is gone (the world then stays paused on the day's report until next_day)."""
        last = self.clock()
        while not self.stop.wait(0.1):
            now = self.clock()
            with self.actors_lock:
                staff = [a for a in self.actors if a.role != 'patient']
            hourly = sum(config.WAGE.get(a.role, 0) * (config.ON_CALL_PAY if a.on_call else 1) for a in staff)
            hour = (now - self.day_start) / config.HOUR_S
            overtime = hour >= config.DAY_HOURS  # past midnight: everyone still here is on overtime
            paid = hourly * (now - last) / config.HOUR_S * (config.OVERTIME_PAY if overtime else 1)
            self.count('wages', paid)
            if overtime:
                self.count('overtime', paid)
            last = now
            if not self.closed and hour >= config.CLOSE_HOUR:
                self.closed = True
                self.log(f'{config.CLOSE_HOUR}:00: no more patients today. The ones inside are seen to by midnight')
            if hour >= config.LAST_CALL_HOUR and not self.sent_home:  # the doctors' last call: from now on, whoever
                self.sent_home = True  # no doctor has yet (but Reds) goes home, back tomorrow (Patient.upkeep)
                self.log('23:30, last call: whoever is still waiting goes home, to come back tomorrow')
            bringing = any(a.role == 'ambulance' or getattr(a, 'bringing', False) for a in self.actors)  # (an
            # ambulance or a car still on its way with someone)
            if self.closed and self.day_over is None and not self.patients() and not bringing:
                self.end_day()

    def today(self):
        """Today's numbers so far (stats since the day started)."""
        with self.stats_lock:
            today = Counter(self.stats)
            today.subtract(self.day_stats)
        return today

    def end_day(self):
        """The day's report, the bonus shared out, and the world paused until next_day."""
        today = self.today()
        with self.stats_lock:
            credit = dict(self.credit)
        profit = today['income'] - today['wages']
        pool = config.BONUS_SHARE * max(0.0, profit)
        total = sum(credit.values())
        bonus = sorted(((pool * c / total, who) for who, c in credit.items()), reverse=True) if total else []
        late = max(0.0, (self.clock() - self.day_start) / config.HOUR_S - config.DAY_HOURS) * 60  # game minutes
        self.day_over = {'day': self.day, 'income': today['income'], 'wages': today['wages'], 'profit': profit,
                         'overtime': today['overtime'], 'overtime_min': late,
                         'bonus': pool, 'treated': today['treated'], 'gave_up': today['gave_up'],
                         'expelled': today['expelled'], 'surgeries': today['surgeries'],
                         'called_in': today['called_in'], 'staff_bonus': bonus}
        self.set_speed(paused=True)
        self.log(f'End of day {self.day}: earned {today["income"]:.0f}, wages {today["wages"]:.0f}, '
                 f'profit {profit:.0f}, staff bonus {pool:.0f}')

    def next_day(self):
        """After the report: the next day starts (from 00:00, doors open)."""
        if self.day_over is None:
            return
        with self.stats_lock:
            self.day_stats, self.credit = Counter(self.stats), Counter()
        self.day, self.day_start, self.closed, self.day_over = self.day + 1, self.clock(), False, None
        self.sent_home = False
        self.set_speed(paused=False)
        self.log(f'Day {self.day} starts')

    def treated(self, p, surgery=False):
        """p pays: by its urgency, less the longer it waited for a doctor (config.PRICE). The payment is credited to
        whoever did each step for it (config.SHARE): that shares the day's bonus out."""
        p.cured = True  # (the UI shows it over its head until it's gone)
        u, on_time = p.base_urgency, config.ON_TIME_S[p.base_urgency]
        late = max(0.0, p.waited - on_time) / (2 * on_time)  # 0 on time, 1 at three times that
        pay = (config.PRICE[u] + (config.SURGERY_PRICE if surgery else 0)) \
            * max(config.LATE_PAYS, 1 - (1 - config.LATE_PAYS) * late)
        shares = {step: share for step, share in config.SHARE.items() if p.helpers.get(step)}
        shares['doctor'] = shares.get('doctor', 0) + 1 - sum(shares.values())  # the steps nobody did
        with self.stats_lock:
            self.stats['treated'] += 1
            self.stats['income'] += pay
            for step, share in shares.items():
                who = p.helpers.get(step, [])
                for a in who:
                    self.credit[a.label()] += pay * share / len(who)

    def credit_for(self, actor, kind):
        """Security and cleaners: what a fight stopped or a mess cleaned counts for the bonus (config.CREDIT)."""
        with self.stats_lock:
            self.credit[actor.label()] += config.CREDIT[kind]
