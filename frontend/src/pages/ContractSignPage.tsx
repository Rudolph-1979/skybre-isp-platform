import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import axios from "axios";
import skybreIcon from "../assets/skybre-icon.png";

// The page a client signs a contract on (6 Oct 2026). Opened from the
// emailed link on their phone, or on a staff device for signing in person
// (?witness=...). No account: the token in the URL is the credential, so
// this deliberately uses its own axios rather than the staff `api` client,
// which would attach a staff JWT and redirect to /login on a 401.
const publicApi = axios.create({ baseURL: "/api" });

type PageData = {
  state: "open" | "signed" | "cancelled" | "expired";
  reference: string;
  title: string;
  kind: "template" | "upload";
  company_name: string;
  company_phone: string;
  company_email: string;
  client_name: string;
  client_company: string;
  id_number: string;
  body_html: string;
  pages: number | null;
  expires_at: string | null;
  in_person: boolean;
  witness_name: string;
  signed_at: string | null;
  signer_name: string;
  signed_copy: boolean;
};

function errorText(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

function when(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString("en-ZA", { day: "2-digit", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

// The wording is written by Skybre staff, but it is shown on the same origin
// as the staff app (and on a staff device when signing in person), so it is
// reduced to plain document markup before it is put on the page.
function sanitize(html: string) {
  const doc = new DOMParser().parseFromString(`<div>${html}</div>`, "text/html");
  doc.querySelectorAll("script,style,iframe,object,embed,link,meta,form,input,button,textarea,select,base").forEach((n) =>
    n.remove(),
  );
  doc.querySelectorAll("*").forEach((el) => {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim().toLowerCase();
      if (name.startsWith("on") || ((name === "href" || name === "src") && !/^(https?:|mailto:|tel:|#|data:image\/)/.test(value))) {
        el.removeAttribute(attr.name);
      }
    }
    if (el.tagName === "A") {
      el.setAttribute("target", "_blank");
      el.setAttribute("rel", "noopener noreferrer");
    }
  });
  return doc.body.firstElementChild?.innerHTML ?? "";
}

function SignaturePad({ onChange }: { onChange: (dataUrl: string | null) => void }) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const drawing = useRef(false);
  const inked = useRef(0);
  const last = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const ratio = window.devicePixelRatio || 1;
    const rect = c.getBoundingClientRect();
    c.width = Math.round(rect.width * ratio);
    c.height = Math.round(rect.height * ratio);
    const ctx = c.getContext("2d");
    if (ctx) {
      ctx.scale(ratio, ratio);
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.lineWidth = 2.5;
      ctx.strokeStyle = "#111111";
    }
  }, []);

  function point(e: ReactPointerEvent<HTMLCanvasElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    return { x: e.clientX - rect.left, y: e.clientY - rect.top };
  }

  function down(e: ReactPointerEvent<HTMLCanvasElement>) {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drawing.current = true;
    last.current = point(e);
  }

  function move(e: ReactPointerEvent<HTMLCanvasElement>) {
    if (!drawing.current || !last.current) return;
    e.preventDefault();
    const ctx = e.currentTarget.getContext("2d");
    const p = point(e);
    if (ctx) {
      ctx.beginPath();
      ctx.moveTo(last.current.x, last.current.y);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
    inked.current += Math.hypot(p.x - last.current.x, p.y - last.current.y);
    last.current = p;
  }

  function up() {
    if (!drawing.current) return;
    drawing.current = false;
    last.current = null;
    onChange(inked.current > 40 && canvas.current ? canvas.current.toDataURL("image/png") : null);
  }

  function clear() {
    const c = canvas.current;
    const ctx = c?.getContext("2d");
    if (c && ctx) ctx.clearRect(0, 0, c.width, c.height);
    inked.current = 0;
    onChange(null);
  }

  return (
    <div>
      <div className="relative rounded-md border-2 border-dashed border-[var(--baseline)] bg-white">
        <canvas
          ref={canvas}
          className="block h-40 w-full touch-none"
          style={{ touchAction: "none" }}
          onPointerDown={down}
          onPointerMove={move}
          onPointerUp={up}
          onPointerCancel={up}
          onPointerLeave={up}
        />
        <span className="pointer-events-none absolute bottom-2 left-3 text-xs text-gray-400">Sign here</span>
      </div>
      <button type="button" onClick={clear} className="mt-1 text-xs font-medium text-[var(--series-1)] hover:underline">
        Clear and draw again
      </button>
    </div>
  );
}

export function ContractSignPage() {
  const { token } = useParams<{ token: string }>();
  const [searchParams] = useSearchParams();
  const witness = searchParams.get("witness") ?? "";
  const [data, setData] = useState<PageData | null>(null);
  const [loadError, setLoadError] = useState("");
  const [name, setName] = useState("");
  const [idNumber, setIdNumber] = useState("");
  const [signature, setSignature] = useState<string | null>(null);
  const [agree, setAgree] = useState(false);
  const [openedPdf, setOpenedPdf] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [emailedTo, setEmailedTo] = useState("");

  const base = `/public/contract/${token}`;

  useEffect(() => {
    publicApi
      .get<PageData>(`${base}/${witness ? `?witness=${encodeURIComponent(witness)}` : ""}`)
      .then((res) => {
        setData(res.data);
        setName(res.data.client_name);
        setIdNumber(res.data.id_number || "");
      })
      .catch((err) => setLoadError(errorText(err, "This signing link isn't valid.")));
  }, [base, witness]);

  async function submit() {
    setSubmitting(true);
    setError("");
    try {
      const res = await publicApi.post<{ signed_at: string; signer_name: string; emailed_to: string }>(`${base}/sign/`, {
        signer_name: name,
        id_number: idNumber,
        signature,
        agree,
        witness: witness || undefined,
      });
      setEmailedTo(res.data.emailed_to);
      setData((d) =>
        d ? { ...d, state: "signed", signed_at: res.data.signed_at, signer_name: res.data.signer_name, signed_copy: true } : d,
      );
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      setError(errorText(err, "Signing didn't work. Please try again."));
    } finally {
      setSubmitting(false);
    }
  }

  const header = (
    <header className="mb-6 flex items-center gap-3">
      <img src={skybreIcon} alt="Skybre" className="h-12 w-12 shrink-0 object-contain" />
      <div>
        <p className="text-sm font-semibold text-[var(--text-primary)]">{data?.company_name || "Skybre"}</p>
        <p className="text-xs text-[var(--text-muted)]">Internet Service Provider</p>
      </div>
    </header>
  );

  if (loadError || !data) {
    return (
      <div className="min-h-screen bg-[var(--surface-0)] px-4 py-8">
        <div className="mx-auto max-w-2xl">
          {header}
          <p className="text-sm text-[var(--text-secondary)]">{loadError || "Loading your contract…"}</p>
        </div>
      </div>
    );
  }

  const contact = [data.company_phone, data.company_email].filter(Boolean).join(" or ");
  const needsPdf = data.kind === "upload";
  const ready = name.trim().length >= 3 && signature && agree && (!needsPdf || openedPdf);

  return (
    <div className="min-h-screen bg-[var(--surface-0)] px-4 py-8">
      <div className="mx-auto max-w-2xl">
        {header}

        <h1 className="text-xl font-semibold text-[var(--text-primary)]">{data.title}</h1>
        <p className="mb-5 text-sm text-[var(--text-muted)]">
          {data.reference} · for {data.client_name}
          {data.client_company && data.client_company !== data.client_name ? ` (${data.client_company})` : ""}
        </p>

        {data.state === "signed" && (
          <section className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            <p className="text-base font-semibold text-[#0ca30c]">Signed — thank you.</p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              Signed by {data.signer_name} on {when(data.signed_at)}.
              {emailedTo ? ` A copy is on its way to ${emailedTo}.` : ""}
            </p>
            {data.signed_copy && (
              <a
                href={`/api${base}/document/`}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-block rounded-md bg-[var(--series-1)] px-4 py-2 text-sm font-medium text-white"
              >
                Download your signed copy
              </a>
            )}
          </section>
        )}

        {data.state === "cancelled" && (
          <p className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5 text-sm text-[var(--text-secondary)]">
            This contract was cancelled and can't be signed any more.{contact ? ` Contact us on ${contact}.` : ""}
          </p>
        )}

        {data.state === "expired" && (
          <p className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5 text-sm text-[var(--text-secondary)]">
            This signing link has expired. Ask us to send it again{contact ? ` — ${contact}` : ""}.
          </p>
        )}

        {data.state === "open" && (
          <>
            {data.in_person && (
              <p className="mb-4 rounded-md bg-[var(--surface-2)] px-3 py-2 text-xs text-[var(--text-secondary)]">
                Signing in person with {data.witness_name}.
              </p>
            )}

            <section className="mb-5 rounded-lg border border-[var(--border-hairline)] bg-white p-5 text-sm text-gray-900">
              {data.kind === "template" && data.body_html ? (
                <div
                  className="contract-body space-y-3 [&_h3]:mt-4 [&_h3]:font-semibold [&_table]:w-full [&_table]:text-xs [&_td]:border-b [&_td]:py-1 [&_td]:pr-2 [&_th]:py-1 [&_th]:pr-2 [&_th]:text-left"
                  dangerouslySetInnerHTML={{ __html: sanitize(data.body_html) }}
                />
              ) : (
                <p>
                  Please open and read the contract{data.pages ? ` (${data.pages} page${data.pages === 1 ? "" : "s"})` : ""} before
                  signing.
                </p>
              )}
              <a
                href={`/api${base}/document/`}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => setOpenedPdf(true)}
                className={`mt-4 inline-block rounded-md px-4 py-2 text-sm font-medium ${
                  needsPdf ? "bg-[var(--series-1)] text-white" : "border border-gray-300 text-gray-800"
                }`}
              >
                {needsPdf ? "Open the contract (PDF)" : "Open as PDF"}
              </a>
            </section>

            <section className="space-y-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
              <h2 className="text-sm font-semibold text-[var(--text-primary)]">Sign</h2>
              <label className="block text-sm">
                <span className="mb-1 block text-[var(--text-secondary)]">Your full name</span>
                <input
                  className="w-full rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-[var(--text-primary)]"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block text-[var(--text-secondary)]">ID or company registration number (optional)</span>
                <input
                  className="w-full rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-[var(--text-primary)]"
                  value={idNumber}
                  onChange={(e) => setIdNumber(e.target.value)}
                />
              </label>
              <div className="text-sm">
                <span className="mb-1 block text-[var(--text-secondary)]">Your signature — draw it with your finger or mouse</span>
                <SignaturePad onChange={setSignature} />
              </div>
              <label className="flex items-start gap-2 text-sm text-[var(--text-primary)]">
                <input type="checkbox" className="mt-1" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
                <span>I have read and agree to the terms of this document, and I agree to sign it electronically.</span>
              </label>
              {needsPdf && !openedPdf && (
                <p className="text-xs text-[var(--text-muted)]">Open the contract above first.</p>
              )}
              {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}
              <button
                type="button"
                disabled={!ready || submitting}
                onClick={submit}
                className="w-full rounded-md bg-[var(--series-1)] px-4 py-3 text-base font-semibold text-white disabled:opacity-50"
              >
                {submitting ? "Signing…" : "Sign the contract"}
              </button>
              <p className="text-xs text-[var(--text-muted)]">
                We record your name, signature, the time, and your device's internet address with the signed copy.
                {data.expires_at ? ` This link works until ${when(data.expires_at)}.` : ""}
                {contact ? ` Questions? ${contact}.` : ""}
              </p>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
