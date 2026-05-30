// ═══════════════════════════════════════════════════════════════
//  Cavalar.IA — Service Worker
//  Propósito: NOTIFICAÇÕES (mostrar no celular + abrir a tela certa
//  ao tocar). NÃO faz cache de fetch de propósito — assim o app nunca
//  serve uma versão velha depois de um deploy no GitHub Pages.
// ═══════════════════════════════════════════════════════════════

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

// Toque na notificação → foca uma aba aberta (ou abre uma) na tela certa.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const destino = (e.notification.data && e.notification.data.destino) || '';
  const base = self.registration.scope; // ex.: https://cavalariapp.github.io/Cavalar.IA/
  const url = destino === 'perfil' ? base + 'perfil.html' : base + 'index.html?go=mensagens';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
      for (const w of wins) {
        // Reusa uma aba do app já aberta
        if (w.url.indexOf(base) === 0 && 'focus' in w) {
          w.focus();
          if ('navigate' in w) { try { w.navigate(url); } catch (_) {} }
          return;
        }
      }
      return self.clients.openWindow(url);
    })
  );
});

// Push do servidor (preparado pro futuro — exige VAPID + backend de envio).
// Sem servidor de push configurado, este handler simplesmente não é chamado.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = {}; }
  const titulo = d.title || 'Cavalar.IA';
  const opts = {
    body: d.body || '',
    icon: 'icons/icon-192.png',
    badge: 'icons/icon-192.png',
    data: d.data || {},
  };
  e.waitUntil(self.registration.showNotification(titulo, opts));
});
