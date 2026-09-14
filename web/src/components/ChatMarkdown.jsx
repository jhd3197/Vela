import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

// Use the renderer's default URL filtering; never execute model-supplied HTML.
const components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ alt }) => <span>{alt ? `[Image: ${alt}]` : '[Image]'}</span>,
  table: ({ children }) => (
    <div className="chat-table">
      <table>{children}</table>
    </div>
  ),
};

export default function ChatMarkdown({ children }) {
  return (
    <div className="chat-markdown">
      <Markdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {children}
      </Markdown>
    </div>
  );
}
