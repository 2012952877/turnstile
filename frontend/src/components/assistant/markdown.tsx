import { useMemo } from "react"
import ReactMarkdown, { type Components, type Options } from "react-markdown"
import remarkBreaks from "remark-breaks"
import remarkGfm from "remark-gfm"
import { ResizableTable } from "../ui/resizable-table"

/** SmartHive's `packages/ui/markdown/Markdown.tsx`, reduced to its `minimal` mode — the
 *  one its own chat window uses. The element map, the plugin pair and the container class
 *  are carried over; the Tailwind utilities become the stylesheet rules beside them.
 *
 *  Four things SmartHive loads are deliberately left out, because each exists to serve a
 *  feature this panel does not have and every one of them is expensive:
 *
 *    rehype-raw + rehype-sanitize   SmartHive injects its own `<div data-type="fileCard">`
 *                                   during preprocessing, so it has to re-enable raw HTML
 *                                   and then sanitize it back. We inject nothing, and
 *                                   react-markdown escapes HTML by default. Adding the
 *                                   pair would open an HTML injection path into
 *                                   model-generated text in exchange for nothing.
 *    rehype-katex + katex           no maths in a usage answer, and the CSS drags fonts.
 *    shiki                          no code in a usage answer, and it is the single
 *                                   largest dependency in SmartHive's markdown stack.
 *    mentions / file cards          concepts this product does not have.
 */

/** Links come from a model, so they are opened defensively: `noopener` closes the reverse
 *  tabnabbing path, and react-markdown's default url transform already drops
 *  `javascript:`-style hrefs before this runs. */
const COMPONENTS: Partial<Components> = {
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  // A 380px panel cannot widen for a table, so the table scrolls inside its own box
  // rather than pushing the thread sideways.
  table: ({ children }) => <div className="assistant-md-table"><ResizableTable>{children}</ResizableTable></div>,
  code: ({ className, children, ...props }) => {
    const block = "node" in props
      && props.node?.position?.start.line !== props.node?.position?.end.line
    if (!block && !/language-(\w+)/.test(className ?? "")) return <code>{children}</code>
    return <pre><code className={className}>{children}</code></pre>
  },
  // The block above already emits its own `pre`; keeping react-markdown's would nest them.
  pre: ({ children }) => <>{children}</>,
}

const REMARK_PLUGINS: Options["remarkPlugins"] = [remarkBreaks, [remarkGfm, { singleTilde: false }]]

export function AssistantMarkdown({ children }: { children: string }) {
  // The thread re-renders on every keystroke in the composer, and re-parsing every past
  // answer to produce the same tree each time is wasted work that grows with the
  // conversation.
  const tree = useMemo(() => <ReactMarkdown
    remarkPlugins={REMARK_PLUGINS}
    components={COMPONENTS}
  >{children}</ReactMarkdown>, [children])
  return <div className="assistant-markdown" data-no-localize>{tree}</div>
}
