// Presentation-only controls; the report serializes calls with its model queue.
window.NanoCoFoldBenchmarkDisplay = class NanoCoFoldBenchmarkDisplay {
  constructor(viewer, components) {
    this.viewer = viewer;
    this.components = components;
    this.residues = [];
    this.created = false;
  }
  async display(options) {
    const plugin = this.viewer.plugin;
    plugin.layout.setProps({regionState: {...plugin.layout.state.regionState,
      top: options.sequence === true ? "full" : "hidden"}});
    this.viewer.handleResize();
    if (options.residues === true && !this.created) {
      for (const {component, color} of this.components) {
        this.residues.push(await plugin.builders.structure.representation.addRepresentation(component,
          {type: "ball-and-stick", color: "uniform", colorParams: {value: color}}));
      }
      this.created = true;
    }
    for (const representation of this.residues)
      plugin.state.data.updateCellState(representation.ref, {isHidden: options.residues !== true});
  }
  clearSelection() {
    const plugin = this.viewer.plugin;
    plugin.managers.interactivity.lociSelects.deselectAll();
    plugin.managers.interactivity.lociHighlights.clearHighlights();
    plugin.managers.structure.focus.clear();
  }
};
