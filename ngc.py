import cgi
import os
import random
import urllib
import datetime
import logging
import re

from google.appengine.api import channel
from google.appengine.api import users
from google.appengine.ext import webapp
from google.appengine.ext.webapp.util import run_wsgi_app
from google.appengine.ext import db
from google.appengine.ext.webapp import template
from google.appengine.api import memcache
from google.appengine.ext.webapp.util import run_wsgi_app

# Establece el nivel de depuración
_DEBUG = True
def generate_random(len):
  word = ''
  for i in range(len):
    word += random.choice('0123456789')
  return word

def sanitize(key):
  return re.sub("[^a-zA-Z0-9\-]", "-", key);

def make_token(room, user):
  return room.key().id_or_name() + '/' + user

def make_pc_config(stun_server):
  if stun_server:
    return "STUN " + stun_server
  else:
    return "STUN stun.l.google.com:19302"

class Room(db.Model):
  """Todos los datos se almacenan en una sala"""


  def __str__(self):
    str = "["
    if self.user1:
      str += self.user1
    if self.user2:
      str += ", " + self.user2
    str += "]"
    return str

  def get_occupancy(self):
    occupancy = 0
    if self.user1:
      occupancy += 1
    if self.user2:
      occupancy += 1
    return occupancy

  def get_other_user(self, user):
    if user == self.user1:
      return self.user2
    elif user == self.user2:
      return self.user1
    else:
      return None

  def has_user(self, user):
    return (user and (user == self.user1 or user == self.user2))

  def add_user(self, user):
    if not self.user1:
      self.user1 = user
    elif not self.user2:
      self.user2 = user
    else:
      raise RuntimeError('room is full')
    self.put()

  def remove_user(self, user):
    if user == self.user2:
      self.user2 = None
    if user == self.user1:
      if self.user2:
        self.user1 = self.user2
        self.user2 = None
      else:
        self.user1 = None
    if self.get_occupancy() > 0:
      self.put()
    else:
      self.delete()


class ConnectPage(webapp.RequestHandler):
  def post(self):
    key = self.request.get('from')
    room_key, user = key.split('/');
    logging.info('User ' + user + ' connected to room ' + room_key)


class DisconnectPage(webapp.RequestHandler):
  def post(self):
    key = self.request.get('from')
    room_key, user = key.split('/');
    logging.info('Removing user ' + user + ' from room ' + room_key)
    room = Room.get_by_key_name(room_key)
    if room and room.has_user(user):
      other_user = room.get_other_user(user)
      room.remove_user(user)
      logging.info('Room ' + room_key + ' has state ' + str(room))
      if other_user:
        channel.send_message(make_token(room, other_user), '{"type":"bye"}')
        logging.info('Sent BYE to ' + other_user)
    else:
      logging.warning('Unknown room ' + room_key)


class MessagePage(webapp.RequestHandler):
  def post(self):
    message = self.request.body
    room_key = self.request.get('r')
    room = Room.get_by_key_name(room_key)
    if room:
      user = self.request.get('u')
      other_user = room.get_other_user(user)
      if other_user:
        # caso especial del escenario loopback
        if other_user == user:
          message = message.replace("\"offer\"", "\"answer\"")
          message = message.replace("a=crypto:0 AES_CM_128_HMAC_SHA1_32",
                                    "a=xrypto:0 AES_CM_128_HMAC_SHA1_32")
        channel.send_message(make_token(room, other_user), message)
        logging.info('Delivered message to user ' + other_user);
    else:
      logging.warning('Unknown room ' + room_key)


class MainPage(webapp.RequestHandler):
  """La página de interfaz de usuario principal, hace que la palntilla 'index.html' """

  def get(self):
    """Presenta la página principal. Cuando esta página se muestra, se crea un nuevo
     canal para empujar actualizaciones asíncronas al cliente."""
    room_key = sanitize(self.request.get('r'));
    debug = self.request.get('debug')
    stun_server = self.request.get('ss');
    if not room_key:
      room_key = generate_random(8)
      redirect = '/?r=' + room_key
      if debug:
        redirect += ('&debug=' + debug)
      if stun_server:
        redirect += ('&ss=' + stun_server)
      self.redirect(redirect)
      logging.info('Redirigiendo ' + redirect)
      return

    user = None
    initiator = 0
    room = Room.get_by_key_name(room_key)
    if not room and debug != "full":
      # Nueva sala.
      user = generate_random(8)
      room = Room(key_name = room_key)
      room.add_user(user)
      if debug != "loopback":
        initiator = 0
      else:
        room.add_user(user)
        initiator = 1
    elif room and room.get_occupancy() == 1 and debug != "full":
      # 1 ocupante.
      user = generate_random(8)
      room.add_user(user)
      initiator = 1
    else:
      # 2 ocupante (lleno).
      path = os.path.join(os.path.dirname(__file__), 'full.html')
      self.response.out.write(template.render(path, { 'room_key': room_key }));
      logging.info('Room ' + room_key + ' is full');
      return

    room_link = 'https://mmor.biz.com/?r=' + room_key
    if debug:
      room_link += ('&debug=' + debug)
    if stun_server:
      room_link += ('&ss=' + stun_server)

    token = channel.create_channel(room_key + '/' + user)
    pc_config = make_pc_config(stun_server)
    template_values = {'token': token,
                       'me': user,
                       'room_key': room_key,
                       'room_link': room_link,
                       'initiator': initiator,
                       'pc_config': pc_config
                      }
    path = os.path.join(os.path.dirname(__file__), 'index.html')
    self.response.out.write(template.render(path, template_values))
    logging.info('User ' + user + ' added to room ' + room_key);
    logging.info('Room ' + room_key + ' has state ' + str(room))


application = webapp.WSGIApplication([
    ('/', MainPage),
    ('/message', MessagePage),
    ('/_ah/channel/connected/', ConnectPage),
    ('/_ah/channel/disconnected/', DisconnectPage)
  ], debug=True)
class GreetingUser(db.Model):
  greeting_user = db.UserProperty()
  joined = db.DateTimeProperty(auto_now_add=True)
  picture = db.StringProperty()
  seated = db.StringProperty()
  website = db.StringProperty()
  
class Greeting(db.Model):
  author = db.UserProperty()
  content = db.StringProperty(multiline=True)
  date = db.DateTimeProperty(auto_now_add=True)

class BaseRequestHandler(webapp.RequestHandler):
 

  def generate(self, template_name, template_values={}):
    """Generar toma renders y plantilla HTML junto con los valores
        pasó a esa plantilla

        args:
          template_name: Una cadena que representa el nombre de la plantilla HTML
          template_values​​: un diccionario que asocia objetos con una cadena
            asignado a ese objeto de llamar a la plantilla HTML. El defualt
            es un diccionario vacío.
    """
    # Comprobamos si hay un usuario actual y generar un ingreso o salida del URL
    user1 = users.get_current_user()
    user2 = users.get_current_user()

    if user:
      log_in_out_url = users.create_logout_url('/')
    else:
      log_in_out_url = users.create_login_url(self.request.path)

    # Vamos a mostrar el nombre de usuario si está disponible y la URL en todas las páginas
    values = {'user': user, 'log_in_out_url': log_in_out_url}
    values.update(template_values)

    # Construir la ruta a la plantilla
    directory = os.path.dirname(__file__)
    path = os.path.join(directory, 'templates', template_name)

    # Responder a la solicitud de la prestación de la plantilla
    self.response.out.write(template.render(path, values, debug=_DEBUG))
    
class MainRequestHandler(BaseRequestHandler):
  def get(self):
    if users.get_current_user():
      url = users.create_logout_url(self.request.uri)
      url_linktext = 'Logout'
    else:
      url = users.create_login_url(self.request.uri)
      url_linktext = 'Login'

    template_values = {
      'url': url,
      'url_linktext': url_linktext,
      }

    self.generate('index.html', template_values);

class ChatsRequestHandler(BaseRequestHandler):
  def renderChats(self):
    greetings_query = Greeting.all().order('date')
    greetings = greetings_query.fetch(1000)

    template_values = {
      'greetings': greetings,
    }
    return self.generate('chats.html', template_values)
      
  def getChats(self, useCache=True):
    if useCache is False:
      greetings = self.renderChats()
      if not memcache.set("chat", greetings, 10):
        logging.error("Memcache set failed:")
      return greetings
      
    greetings = memcache.get("chats")
    if greetings is not None:
      return greetings
    else:
      greetings = self.renderChats()
      if not memcache.set("chat", greetings, 10):
        logging.error("Memcache set failed:")
      return greetings
    
  def get(self):
    self.getChats()

  def post(self):
    greeting = Greeting()

    if users.get_current_user():
      greeting.author = users.get_current_user()

    greeting.content = self.request.get('content')
    greeting.put()
    
    self.getChats(False)

    
class EditUserProfileHandler(BaseRequestHandler):
  """Esto permite al usuario editar su perfil wiki. El usuario puede subir
      una imagen y establecer una URL del feed de datos personales
  """
  def get(self, user):
    # Obtener la información de los usuarios
    unescaped_user = urllib.unquote(user)
    greeting_user_object = users.User(unescaped_user)
    # Sólo  el usuario puede editar su perfil
    if users.get_current_user() != greeting_user_object:
      self.redirect('/view/StartPage')

    greeting_user = GreetingUser.gql('WHERE greeting_user = :1', greeting_user_object).get()
    if not greeting_user:
      greeting_user = GreetingUser(greeting_user=greeting_user_object)
      greeting_user.put()

    self.generate('edit_user.html', template_values={'queried_user': greeting_user})

  def post(self, user):
    # Obtener la información de los usuarios
    unescaped_user = urllib.unquote(user)
    greeting_user_object = users.User(unescaped_user)
    # Sólo el usuario puede editar su perfil
    if users.get_current_user() != greeting_user_object:
      self.redirect('/')

    greeting_user = GreetingUser.gql('WHERE greeting_user = :1', greeting_user_object).get()

    greeting_user.picture = self.request.get('user_picture')
    greeting_user.website = self.request.get('user_website')
    greeting_user.seated = self.request.get('user_seated')
    greeting_user.put()


    self.redirect('/user/%s' % user)
    
class UserProfileHandler(BaseRequestHandler):
  """Permite a un usuario ver el perfil de otro usuario. Todos los usuarios pueden
      ver esta información, mediante la solicitud
  """

  def get(self, user):
    """Cuando se solicita la URL, nos encontramos con la información de ese usuario WikiUser.
        También recuperar artículos escritos por el usuario
    """
    # Webob sobre cita el URI de la solicitud, así que tenemos que dijeron ellos dos veces
    unescaped_user = urllib.unquote(urllib.unquote(user))

    # consulta de la información de usuario
    greeting_user_object = users.User(unescaped_user)
    greeting_user = GreetingUser.gql('WHERE greeting_user = :1', greeting_user_object).get()

    # Generar el perfil de usuario
    self.generate('user.html', template_values={'queried_user': greeting_user})

                                                
application = webapp.WSGIApplication(
                                     [('/', MainRequestHandler),
                                      ('/getchats', ChatsRequestHandler),
                                      ('/user/([^/]+)', UserProfileHandler),
                                      ('/edituser/([^/]+)', EditUserProfileHandler)],
                                     debug=True)

def main():
  run_wsgi_app(application)

if __name__ == "__main__":
  main()
  
