import {
  Bars,
  Card,
  KeyValue,
  Meter,
  Pill,
  Ring,
  Rows,
  SegControl,
  Sparkline,
  Stat,
  Tag,
} from '../../src/components/ds/index.js';
import { TONES } from './tones.js';

// Every primitive, in every tone, drawn twice -- once in each base. The point
// is not that it looks right but that its colours come from the token sheet:
// the suite reads the computed style of each mark and compares it against the
// custom property it should have resolved from, which no amount of rendering
// in one theme can prove.
//
// Browser-only fixture; no Vela API calls, user data, or production entry point.
const SERIES = [3, 7, 4, 9, 6, 11, 8];

function Gallery({ base }) {
  return (
    <section data-theme={base} data-gallery={base} className="primitive-gallery">
      <h2>{base}</h2>
      {TONES.map((tone) => (
        <Card
          key={tone}
          tone={tone}
          icon={<span aria-hidden="true">◆</span>}
          title={`Card ${tone}`}
          meta="2m"
          data-case={`card-${tone}`}
          footer={<Tag tone={tone}>{tone}</Tag>}
        >
          <Stat value="42" unit="GB" delta="+4%" deltaTone={tone} caption="since Monday" />
          <Meter percent={62} label="Memory" detail="6 / 10 GB" tone={tone} />
          <Ring percent={78} caption="checks" tone={tone} />
          <Bars series={SERIES} caption="this week" />
          <Sparkline series={SERIES} tone={tone} label={`${tone} trend`} />
          <KeyValue
            rows={[
              { label: 'Storage', value: '2.5 TB' },
              { label: 'Failures', value: '2', tone },
            ]}
          />
          <Rows
            rows={[
              { id: 'a', label: 'Health', detail: 'checked', tail: '2m', tone },
              { id: 'b', label: 'Notes', detail: 'idle', tail: '1h' },
            ]}
          />
          <Pill tone={tone}>Running</Pill>
          <Tag>wellness</Tag>
        </Card>
      ))}
      <SegControl
        label={`Base ${base}`}
        value="dark"
        options={[
          { value: 'light', label: 'Light' },
          { value: 'dark', label: 'Dark' },
        ]}
      />
      <p className="vela-empty">Nothing here yet.</p>
      <p className="vela-empty vela-empty-error">Could not load this.</p>
    </section>
  );
}

export default function PrimitiveFixtures() {
  return (
    <>
      <Gallery base="light" />
      <Gallery base="dark" />
    </>
  );
}
