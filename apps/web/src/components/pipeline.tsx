const stages = [
  {
    name: "Qualification",
    deals: 28,
    value: "$840K",
    percent: 72,
  },
  {
    name: "Discovery",
    deals: 17,
    value: "$620K",
    percent: 58,
  },
  {
    name: "Demo",
    deals: 12,
    value: "$710K",
    percent: 48,
  },
  {
    name: "Proposal",
    deals: 8,
    value: "$1.1M",
    percent: 36,
  },
  {
    name: "Negotiation",
    deals: 5,
    value: "$760K",
    percent: 24,
  },
];

export function Pipeline() {
  return (
    <section className="panel">
      <div className="panelHeader">
        <div>
          <h2>Sales Pipeline</h2>
          <p>Current opportunity distribution</p>
        </div>

        <button className="textButton" type="button">
          View pipeline
        </button>
      </div>

      <div className="pipelineList">
        {stages.map((stage) => (
          <div className="pipelineRow" key={stage.name}>
            <div className="pipelineStage">
              <strong>{stage.name}</strong>
              <span>{stage.deals} opportunities</span>
            </div>

            <div className="pipelineBar">
              <div
                className="pipelineProgress"
                style={{ width: `${stage.percent}%` }}
              />
            </div>

            <strong className="pipelineValue">{stage.value}</strong>
          </div>
        ))}
      </div>
    </section>
  );
}
