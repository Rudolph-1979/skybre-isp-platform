import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import { formatIsoDateTime } from "../utils/billingTerms";

/**
 * Coverage imported from KML / KMZ files (#178, Rudolph 15 Oct 2026). The
 * card on Networking -> Map lists what is loaded; Admin / Management import
 * (pick the file, tick the folders, import), switch layers off and delete.
 * The map shows only a dot per serviceable house.
 */

export type CoverageLayer = {
  id: number;
  name: string;
  provider: string;
  kind: "fibre" | "wireless";
  active: boolean;
  areas: number;
  houses: number;
  folders: string[];
  source_file: string;
  imported_at: string;
  imported_by: string;
};
type Folder = { path: string; areas: number; houses: number; wall_boxes: number; excluded: boolean; default: boolean; note: string };

export function CoverageLayersCard({ onChanged }: { onChanged: () => void }) {
  const [layers, setLayers] = useState<CoverageLayer[]>([]);
  const [canManage, setCanManage] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  function load() {
    api
      .get<{ results: CoverageLayer[]; can_manage: boolean }>("/map/coverage/layers/")
      .then((r) => {
        setLayers(r.data.results);
        setCanManage(r.data.can_manage);
      })
      .catch(() => setError("Couldn't load the coverage layers."));
  }
  useEffect(load, []);

  async function toggle(l: CoverageLayer) {
    await api.patch(`/map/coverage/layers/${l.id}/`, { active: !l.active });
    load();
    onChanged();
  }
  async function remove(l: CoverageLayer) {
    if (!window.confirm(`Delete "${l.name}" (${l.houses} houses, ${l.areas} areas)? Import the file again to bring it back.`)) return;
    await api.delete(`/map/coverage/layers/${l.id}/`);
    load();
    onChanged();
  }

  return (
    <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">Coverage</p>
        {canManage && (
          <button type="button" className="text-xs text-[var(--series-1)] hover:underline" onClick={() => setImporting(true)}>
            Import KML / KMZ
          </button>
        )}
      </div>
      {error && <p className="text-xs text-[var(--status-critical)]">{error}</p>}
      {layers.length === 0 && <p className="text-xs text-[var(--text-muted)]">Nothing imported yet.</p>}
      <ul className="space-y-1.5 text-xs">
        {layers.map((l) => (
          <li key={l.id} className="rounded-md border border-[var(--border-hairline)] px-2 py-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className={`font-medium ${l.active ? "" : "text-[var(--text-muted)] line-through"}`}>{l.name}</span>
              {canManage && (
                <span className="flex gap-2">
                  <button type="button" className="text-[var(--text-muted)] hover:text-[var(--text-primary)]" onClick={() => toggle(l)}>
                    {l.active ? "Switch off" : "Switch on"}
                  </button>
                  <button type="button" className="text-[var(--status-critical)] hover:underline" onClick={() => remove(l)}>
                    Delete
                  </button>
                </span>
              )}
            </div>
            <p className="text-[var(--text-muted)]">
              {l.provider ? `${l.provider} · ` : ""}
              {l.kind} · {l.houses.toLocaleString()} houses · {l.areas.toLocaleString()} areas · {formatIsoDateTime(l.imported_at)}
              {l.imported_by ? ` by ${l.imported_by}` : ""}
            </p>
          </li>
        ))}
      </ul>
      {importing && (
        <ImportModal
          layers={layers}
          onClose={() => setImporting(false)}
          onDone={() => {
            load();
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function ImportModal({ layers, onClose, onDone }: { layers: CoverageLayer[]; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [provider, setProvider] = useState("Vuma");
  const [kind, setKind] = useState<"fibre" | "wireless">("fibre");
  const [replace, setReplace] = useState("");
  const [folders, setFolders] = useState<Folder[] | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  async function read() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await api.post<{ folders: Folder[] }>("/map/coverage/preview/", form);
      setFolders(r.data.folders);
      setTicked(new Set(r.data.folders.filter((f) => f.default).map((f) => f.path)));
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "Couldn't read that file.");
    } finally {
      setBusy(false);
    }
  }
  async function doImport() {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("folders", JSON.stringify([...ticked]));
      form.append("name", name || file.name.replace(/\.km[lz]$/i, ""));
      form.append("provider", provider);
      form.append("kind", kind);
      if (replace) form.append("replace", replace);
      const r = await api.post<CoverageLayer & { dropped_excluded_region: number }>("/map/coverage/import/", form);
      setDone(
        `Imported ${r.data.houses.toLocaleString()} houses and ${r.data.areas.toLocaleString()} areas.` +
          (r.data.dropped_excluded_region ? ` ${r.data.dropped_excluded_region} in Western Cape / KZN / Eastern Cape were left out.` : ""),
      );
      onDone();
    } catch (err: any) {
      setError(err?.response?.data?.detail ?? "The import failed.");
    } finally {
      setBusy(false);
    }
  }
  const sum = (k: "houses" | "wall_boxes" | "areas") => (folders ?? []).filter((f) => ticked.has(f.path)).reduce((a, f) => a + f[k], 0);

  return (
    <Modal title="Import coverage (KML / KMZ)" onClose={onClose}>
      {done ? (
        <div className="space-y-3 text-sm">
          <p>{done}</p>
          <button type="button" className={btnPrimary} onClick={onClose}>
            Close
          </button>
        </div>
      ) : (
        <div className="space-y-3 text-sm">
          <FormField label="File">
            <input
              type="file"
              accept=".kml,.kmz"
              className="text-xs"
              onChange={(e) => {
                setFile(e.target.files?.[0] ?? null);
                setFolders(null);
              }}
            />
          </FormField>
          <p className="text-[11px] text-[var(--text-muted)]">
            A dot goes on the map for every house: the end of each Home Drop, or the Wall Box where a suburb has no home drops. Polygons
            are used for the check only. Western Cape, Durban and Eastern Cape are never imported.
          </p>
          {!folders ? (
            <button type="button" className={btnPrimary} disabled={!file || busy} onClick={read}>
              {busy ? "Reading…" : "Read file"}
            </button>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                <FormField label="Name">
                  <input className={inputClass} value={name} placeholder={file?.name.replace(/\.km[lz]$/i, "")} onChange={(e) => setName(e.target.value)} />
                </FormField>
                <FormField label="Provider">
                  <input className={inputClass} value={provider} onChange={(e) => setProvider(e.target.value)} />
                </FormField>
                <FormField label="Type">
                  <select className={inputClass} value={kind} onChange={(e) => setKind(e.target.value as "fibre" | "wireless")}>
                    <option value="fibre">Fibre</option>
                    <option value="wireless">Wireless</option>
                  </select>
                </FormField>
                <FormField label="Replaces">
                  <select className={inputClass} value={replace} onChange={(e) => setReplace(e.target.value)}>
                    <option value="">Nothing (new layer)</option>
                    {layers.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.name}
                      </option>
                    ))}
                  </select>
                </FormField>
              </div>
              <ul className="max-h-72 space-y-1 overflow-y-auto rounded-md border border-[var(--border-hairline)] p-2 text-xs">
                {folders.map((f) => (
                  <li key={f.path}>
                    <label className={`flex items-start gap-2 ${f.excluded ? "opacity-50" : ""}`}>
                      <input
                        type="checkbox"
                        disabled={f.excluded}
                        checked={ticked.has(f.path)}
                        onChange={(e) => {
                          const next = new Set(ticked);
                          if (e.target.checked) next.add(f.path);
                          else next.delete(f.path);
                          setTicked(next);
                        }}
                      />
                      <span>
                        <span className="font-medium">{f.path}</span>
                        <span className="ml-1 text-[var(--text-muted)]">
                          {[f.houses && `${f.houses} houses`, f.wall_boxes && `${f.wall_boxes} wall boxes`, f.areas && `${f.areas} areas`].filter(Boolean).join(" · ") || "empty"}
                        </span>
                        {f.note && <span className="block text-[var(--text-muted)]">{f.note}</span>}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs text-[var(--text-muted)]">
                  Ticked: {(sum("houses") + sum("wall_boxes")).toLocaleString()} house dots · {sum("areas").toLocaleString()} areas
                </span>
                <span className="flex gap-2">
                  <button type="button" className={btnSecondary} onClick={onClose}>
                    Cancel
                  </button>
                  <button type="button" className={btnPrimary} disabled={busy || ticked.size === 0} onClick={doImport}>
                    {busy ? "Importing…" : "Import"}
                  </button>
                </span>
              </div>
            </>
          )}
          {error && <p className="text-xs text-[var(--status-critical)]">{error}</p>}
        </div>
      )}
    </Modal>
  );
}
