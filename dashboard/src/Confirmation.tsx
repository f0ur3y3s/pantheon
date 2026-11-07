import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

type Request = { title: string; message: string; action: string };

export function useConfirmation() {
  const [request, setRequest] = useState<Request | null>(null);
  const resolve = useRef<((confirmed: boolean) => void) | null>(null);
  const confirm = useCallback((next: Request) => new Promise<boolean>(done => {
    resolve.current?.(false);
    resolve.current = done;
    setRequest(next);
  }), []);
  const finish = useCallback((confirmed: boolean) => {
    resolve.current?.(confirmed);
    resolve.current = null;
    setRequest(null);
  }, []);
  useEffect(() => () => { resolve.current?.(false); }, []);
  return { confirm, confirmation: request ? <Confirmation request={request} finish={finish}/> : null };
}

function Confirmation({ request, finish }: { request: Request; finish: (confirmed: boolean) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const id = useId();
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    dialog.current?.showModal();
    cancel.current?.focus();
    return () => { dialog.current?.close(); if (opener?.isConnected) opener.focus(); };
  }, []);
  return createPortal(<dialog ref={dialog} className="confirmation" aria-labelledby={`${id}-title`} aria-describedby={`${id}-message`}
    onCancel={e => { e.preventDefault(); finish(false); }} onKeyDown={e => e.stopPropagation()}>
    <h2 id={`${id}-title`}>{request.title}</h2>
    <p id={`${id}-message`}>{request.message}</p>
    <div className="buttons"><button ref={cancel} onClick={() => finish(false)}>Cancel</button><button className="danger" onClick={() => finish(true)}>{request.action}</button></div>
  </dialog>, document.body);
}
