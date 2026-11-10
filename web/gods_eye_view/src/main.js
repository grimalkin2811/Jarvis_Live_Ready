/**
 * God's Eye View — Jarvis module entry point.
 * Boot order: viewer (governed) → base imagery → flights layer (idle) →
 * bridge → ready. No layer is enabled until the host asks.
 */
import { createViewer, createBaseImagery } from './viewer.js';
import { createFlightsLayer } from './flights.js';
import { installBridge } from './bridge.js';
import {
  getRenderGovernorDiagnostics,
  governorRequestRender,
} from './vendor/renderGovernor.js';

const governorFacade = {
  diagnostics: () => getRenderGovernorDiagnostics(),
};

function setHudStatus(text, mode) {
  const el = document.getElementById('jarvis-status-text');
  if (el) el.textContent = text;
  const dot = document.getElementById('jarvis-status-dot');
  if (dot) dot.dataset.mode = mode || 'ok';
}

function setLayerChip(state) {
  const chip = document.getElementById('jarvis-layer-chip');
  if (!chip) return;
  if (state.enabled) {
    const suffix = state.source === 'synthetic-sample' ? ' · ÉCHANTILLON' : '';
    const benchmarkSuffix = state.source === 'synthetic-benchmark' ? ' · BENCHMARK' : '';
    chip.hidden = false;
    chip.textContent = `FLIGHTS — ${state.objectCount}${suffix}${benchmarkSuffix}`;
    chip.dataset.warn = state.source === 'synthetic-sample' || state.source === 'synthetic-benchmark'
      ? 'true'
      : 'false';
  } else {
    chip.hidden = true;
    chip.textContent = '';
  }
}

async function boot() {
  setHudStatus('INITIALISATION…', 'busy');
  const viewer = createViewer('jarvis-globe');
  const flights = createFlightsLayer(viewer, {
    onChange: (state) => {
      setLayerChip(state);
      if (state.lastError && state.source === 'synthetic-sample') {
        setHudStatus('HORS LIGNE — ÉCHANTILLON', 'warn');
      } else if (state.source === 'synthetic-benchmark') {
        setHudStatus('BENCHMARK SYNTHÉTIQUE', 'warn');
      } else if (state.enabled) {
        setHudStatus('EN DIRECT · adsb.lol', 'ok');
      } else {
        setHudStatus('PRÊT', 'ok');
      }
    },
  });
  const bridge = installBridge({
    viewer,
    governor: governorFacade,
    layers: { flights },
  });

  const base = await createBaseImagery(viewer);
  bridge.setBaseLayer(base);
  bridge.markReady();
  governorRequestRender('boot-complete');
  setHudStatus('PRÊT', 'ok');

  // Fermeture propre : Échap ou bouton — la décision revient à Jarvis.
  const requestClose = () => {
    try {
      window.jarvisHost?.closeRequested?.();
    } catch {
      /* l'hôte gère son propre cycle de vie */
    }
  };
  document.getElementById('jarvis-close')?.addEventListener('click', requestClose);
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') requestClose();
  });
}

boot().catch((error) => {
  console.error('[GodsEyeView] échec de démarrage', error);
  setHudStatus('ERREUR DE DÉMARRAGE', 'error');
});
