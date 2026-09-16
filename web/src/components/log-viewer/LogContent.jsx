import { forwardRef, useCallback, useLayoutEffect, useRef } from 'react';
import { severityOf, splitOnMatch } from './logHelpers.js';

// The lines themselves. Tail-following is lifted from ServerKit's
// `log-viewer/LogContent.jsx` (MIT, same owner): the viewer sticks to the
// newest line until the reader scrolls up, and re-arms when they come back
// down, so a refresh never yanks someone away from what they were reading.
const LogContent = forwardRef(function LogContent(
  { lines, loading, empty, pattern, scrollKey },
  ref,
) {
  const innerRef = useRef(null);
  const followRef = useRef(true);

  const setRefs = useCallback(
    (node) => {
      innerRef.current = node;
      if (typeof ref === 'function') ref(node);
      else if (ref) ref.current = node;
    },
    [ref],
  );

  const handleScroll = useCallback(() => {
    const element = innerRef.current;
    if (!element) return;
    followRef.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
  }, []);

  // A different log: land on its newest line and follow again.
  useLayoutEffect(() => {
    followRef.current = true;
    const element = innerRef.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [scrollKey]);

  useLayoutEffect(() => {
    const element = innerRef.current;
    if (element && followRef.current) element.scrollTop = element.scrollHeight;
  }, [lines]);

  if (!loading && (!lines || lines.length === 0)) {
    return (
      <div className="logs-content logs-content-empty" ref={setRefs}>
        <p className="panel-note">{empty}</p>
      </div>
    );
  }

  return (
    <div
      className="logs-content"
      ref={setRefs}
      onScroll={handleScroll}
      tabIndex={0}
      role="log"
      aria-label="Log lines"
      aria-busy={loading || undefined}
    >
      <ol className="logs-lines">
        {(lines || []).map((line, index) => {
          const severity = severityOf(line);
          return (
            <li
              key={index}
              className={`logs-line${severity ? ` logs-line-${severity}` : ''}`}
              data-severity={severity || undefined}
            >
              <span className="logs-line-number" aria-hidden="true">
                {index + 1}
              </span>
              <span className="logs-line-text">
                {splitOnMatch(line, pattern).map((part, partIndex) =>
                  part.match ? (
                    <mark key={partIndex}>{part.text}</mark>
                  ) : (
                    <span key={partIndex}>{part.text}</span>
                  ),
                )}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
});

export default LogContent;
