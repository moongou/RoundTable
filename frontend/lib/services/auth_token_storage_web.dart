import 'package:web/web.dart' as web;

String? readToken(String key) {
  try {
    return web.window.localStorage.getItem(key);
  } catch (_) {
    return null;
  }
}

void writeToken(String key, String token) {
  try {
    web.window.localStorage.setItem(key, token);
  } catch (_) {}
}

void removeToken(String key) {
  try {
    web.window.localStorage.removeItem(key);
  } catch (_) {}
}
