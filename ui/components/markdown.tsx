import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

// Run files hold text scraped from the web, so: no raw HTML, no images (they'd load remote
// content), and only web links, which open in a new tab.
const components: Components = {
  img: ({ alt }) => <span className="text-muted-foreground">[image{alt ? `: ${alt}` : ""}]</span>,
  a: ({ href, children }) =>
    href && /^(https?:|mailto:)/i.test(href) ? (
      <a href={href} target="_blank" rel="noreferrer noopener">
        {children}
      </a>
    ) : (
      <span>{children}</span>
    ),
}

// Reports use Pandoc fenced divs (`::: {class="…"}` … `:::`), which aren't Markdown; drop the fence
// lines so the text inside reads normally.
const PANDOC_FENCE = /^ {0,3}:{3,}.*$/gm

export function Markdown({ text }: { text: string }) {
  return (
    <div className="doc-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml components={components}>
        {text.replace(PANDOC_FENCE, "")}
      </ReactMarkdown>
    </div>
  )
}
