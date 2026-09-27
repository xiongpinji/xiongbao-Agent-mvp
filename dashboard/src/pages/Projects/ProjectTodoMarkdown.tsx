import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";

interface Props {
  content: string;
  className?: string;
}

const ALLOWED_ELEMENTS = [
  "p",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "strong",
  "em",
  "ul",
  "ol",
  "li",
  "blockquote",
  "pre",
  "code",
  "a",
  "br",
];

function safeHttpHref(href: string | undefined): string | undefined {
  if (!href || !/^https?:\/\//i.test(href)) return undefined;
  try {
    const url = new URL(href);
    return url.protocol === "http:" || url.protocol === "https:"
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

function SafeLink({ href, children }: { href?: string; children?: ReactNode }) {
  const safeHref = safeHttpHref(href);
  return safeHref ? (
    <a href={safeHref} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ) : (
    <>{children}</>
  );
}

/** Project todo Markdown has a deliberately smaller grammar than chat Markdown. */
export default function ProjectTodoMarkdown({ content, className }: Props) {
  return (
    <div className={className}>
      <ReactMarkdown
        skipHtml
        allowedElements={ALLOWED_ELEMENTS}
        urlTransform={(url) => safeHttpHref(url) ?? ""}
        components={{ a: SafeLink }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
