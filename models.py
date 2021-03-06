# -*- coding: utf-8 -*-

import datetime
import hashlib
import copy
import time
import relativedelta
import elo
from datetime import date
from google.appengine.ext import db
from google.appengine.api import memcache
from google.appengine.api import users

SCORE = [ 0, 1, 2, 3 ]
DEFAULT_SCORE = 500.0

class Match(db.Model):
  date = db.DateProperty(auto_now_add=True)
  player1 = db.UserProperty()
  player2 = db.UserProperty()
  score1 = db.IntegerProperty(choices=SCORE)
  score2 = db.IntegerProperty(choices=SCORE)


class User(db.Model):
  user = db.UserProperty()
  score = db.FloatProperty(default=DEFAULT_SCORE)
  wins = db.IntegerProperty(default=0)
  loses = db.IntegerProperty(default=0)
  rank = db.IntegerProperty(default=0)


class Score(db.Model):
  user = db.UserProperty()
  date = db.DateProperty()
  score = db.FloatProperty(default=DEFAULT_SCORE)
  

def relative_time(date):
  delta = relativedelta.relativedelta(date.today(), date)
  if delta.years == 1:
    time = 'Il y a environ 1 an'
  elif delta.years > 1:
    time = 'Il y a environ %s ans' % delta.years
  elif delta.months == 1:
    time = 'Il y a environ 1 mois'
  elif delta.months > 1:
    time = 'Il y a environ %s mois' % delta.months
  elif delta.days == 1:
    time = "Hier"
  elif delta.days > 1:
    time = 'Il y a %s jours' % delta.days
  else:
    time = 'Aujourd\'hui'
  return time


def rfc3339date(date):
  """Formats the given date in RFC 3339 format for feeds."""
  if not date: return ''
  date = date + datetime.timedelta(seconds=-time.timezone)
  if time.daylight:
    date += datetime.timedelta(seconds=time.altzone)
  return date.strftime('%Y-%m-%dT%H:%M:%SZ')


def is_registered(user):
  if User.all().filter('user =', user).get() is None:
    return False
  return True


def register_user(user):
  if user is None or User.all().filter('user =', user).get():
    return
  else:
    user_entry = User()
    user_entry.user = user
    user_entry.put()
    return


def get_possible_opponents():
  return User.all().order('user').fetch(100)


def get_possible_opponents_by_rank():
  return User.all().filter('score !=', 500.0).order('-score').fetch(100)


def get_new_players():
  return User.all().filter('score =', 500.0).fetch(100)


def create_new_match(me, request):
  match = Match()
  match.player1 = me
  match.player2 = User.get_by_id(long(request.get('player2'))).user
  match.score1 = long(request.get('score1'))
  match.score2 = long(request.get('score2'))
  match.date = date.today() - datetime.timedelta(days=long(request.get('date')))

  # check score is ok
  if (match.score1 != 3 and match.score2 != 3) or (match.score1 == 3 and match.score2 == 3):
    return None

  match.put()
  return match.key().id()


def get_recent_matches(n=10):
  return Match.all().order('-date').fetch(n)


def get_user(userid):
  return User.get_by_id(long(userid))


def match_compare(x, y):
  if x.date > y.date:
    return -1
  elif x.date == y.date:
    return 0
  else:
    return 1


def get_user_matches(user):
  matches = Match.all().order('-date').filter('player1 =', user).fetch(100) \
            + Match.all().order('-date').filter('player2 =', user).fetch(100)
  matches.sort(match_compare)
  return matches


def get_last_score(user, date):
  score_obj = Score.all().filter('date <=', date).filter('user =', user).order('-date').get()
  if not score_obj:
    return DEFAULT_SCORE
  else:
    return score_obj.score


def get_scores(userid):
  user = User.get_by_id(userid)
  if user is None:
    return None

  scores = Score.all().filter('user =', user.user).fetch(100)
  return scores


def get_winner_looser(match):
  if match.score1 > match.score2:
    return [ match.player1, match.player2, abs(match.score1 - match.score2) ]
  else:
    return [ match.player2, match.player1, abs(match.score1 - match.score2) ]


def update_or_create_score(score, user, date, win=True):
  # update or create Score object
  score_obj = Score.all().filter('date =', date).filter('user =', user).get()
  if not score_obj:
    score_obj = Score()
    score_obj.date = date
    score_obj.user = user
  score_obj.score = float(score)
  score_obj.put()
  # update User object
  user_obj = User.all().filter('user =', user).get()
  user_obj.score = float(score)
  user_obj.put()

def update_wins_loses(winner, looser):
  winner_obj = User.all().filter('user =', winner).get()
  looser_obj = User.all().filter('user =', looser).get()
  winner_obj.wins += 1
  looser_obj.loses += 1
  db.put([ winner_obj, looser_obj ])

def compute_ranks():
  users = User.all().filter('score !=', 500.0).order('-score').fetch(1000) # we suppose we will never have that much users..
  rank = 0
  previous_user_score = 0
  for user in users:
    if user.score != previous_user_score:
      rank += 1
      previous_user_score = user.score
    user.rank = rank
  db.put(users)

def update_scores(match_id):
  current_match = Match.get_by_id(match_id)
  
  # update wins and loses of the players
  [ winner, looser, gap ] = get_winner_looser(current_match)
  update_wins_loses(winner, looser)

  # get all matches that took place after this one 
  # FIXME what if some matches happen the same day? - we don't really care
  matches = Match.all().order('date').filter('date >=', current_match.date).fetch(100)

  # erase scores that need to be re-computed ### C'est ca qui marche pas
  obsolete_scores = Score.all().filter('date >=', current_match.date).fetch(1000)
  db.delete(obsolete_scores)
  #return

  for match in matches:
    [ winner, looser, gap ] = get_winner_looser(match)
    winner_previous_score = get_last_score(winner, match.date)
    looser_previous_score = get_last_score(looser, match.date)
    [ winner_new_score, looser_new_score ] = elo.compute_score(winner_previous_score, looser_previous_score, gap)
    #print 'old win' + str(winner_previous_score)
    #print 'old lose' + str(looser_previous_score)

    update_or_create_score(winner_new_score, winner, match.date, True)
    update_or_create_score(looser_new_score, looser, match.date, False)
    #print 'new win' + str(winner_new_score)
    #print 'new lose' + str(looser_new_score)

  compute_ranks()
