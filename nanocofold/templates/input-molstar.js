/* Mol* 5.12.0 input viewer. Uses normalized server coordinates and chain IDs. */
"use strict";
window.NanoCoFoldChainViewer = class {
  static async create(host, pdb, chains, onPick, onHover) {
    const viewer = await molstar.Viewer.create(host, {
      extensions: [],
      layoutIsExpanded: false,
      layoutShowControls: false,
      layoutShowRemoteState: false,
      layoutShowSequence: false,
      layoutShowLog: false,
      layoutShowLeftPanel: false,
      viewportShowExpand: false,
      viewportShowToggleFullscreen: false,
      viewportShowControls: false,
      viewportShowSelectionMode: false,
      viewportShowAnimation: false,
      viewportShowScreenshotControls: false,
      viewportFocusBehavior: "disabled",
      volumeStreamingDisabled: true,
      viewportBackgroundColor: "#f7fafb",
    });
    const result = new NanoCoFoldChainViewer(viewer, chains);
    try {
      const plugin = viewer.plugin;
      const data = await plugin.builders.data.rawData({ data: pdb, label: "검토할 입력 구조" });
      const trajectory = await plugin.builders.structure.parseTrajectory(data, "pdb");
      const model = await plugin.builders.structure.createModel(trajectory);
      // The server has already expanded the requested assembly and normalized IDs.
      // Do not generate symmetry mates or choose another assembly in the browser.
      const structure = await plugin.builders.structure.createStructure(model, { name: "model", params: {} });
      result.structure = structure;
      const { StructureElement } = molstar.lib.structure;
      for (const chain of chains) {
        const loci = StructureElement.Loci.fromSchema(structure.obj.data, { auth_asym_id: chain.id });
        const component = await plugin.builders.structure.tryCreateComponent(structure, {
          type: { name: "bundle", params: StructureElement.Bundle.fromLoci(loci) },
          nullIfEmpty: true,
          label: `체인 ${chain.id} · 원본 ${chain.original_id}`,
        }, `input-chain-${chain.id}`);
        if (!component) throw Error(`구조에서 체인 ${chain.id}를 찾지 못했습니다.`);
        const representation = await plugin.builders.structure.representation.addRepresentation(component, {
          type: chain.length < 4 ? "ball-and-stick" : "cartoon",
          color: "uniform",
          colorParams: { value: 0xabbac5 },
          typeParams: { alpha: 0.55 },
        });
        result.representations.set(chain.id, representation);
      }
      viewer.subscribe(plugin.behaviors.interaction.click, (event) => {
        const keys = event.modifiers;
        if (result.disposed || !(keys?.control || keys?.meta) || keys.alt || event.button !== 1) return;
        const location = result.location(event.current?.loci);
        if (location) onPick(location.chain.id, keys.shift ? "partner" : "target");
      });
      viewer.subscribe(plugin.behaviors.interaction.hover, (event) => {
        if (result.disposed) return;
        onHover(result.location(event.current?.loci));
      });
      result.fit();
      return result;
    } catch (error) {
      result.dispose();
      throw error;
    }
  }

  constructor(viewer, chains) {
    this.viewer = viewer;
    this.chains = new Map(chains.map((chain) => [chain.id, chain]));
    this.representations = new Map();
    this.disposed = false;
    this.pending = Promise.resolve();
  }

  location(loci) {
    const { StructureElement, StructureProperties, Bond } = molstar.lib.structure;
    if (loci?.kind === "bond-loci") loci = Bond.toStructureElementLoci(loci);
    if (!loci || !StructureElement.Loci.is(loci)) return null;
    const location = StructureElement.Loci.getFirstLocation(loci);
    if (!location) return null;
    const chain = this.chains.get(StructureProperties.chain.auth_asym_id(location));
    if (!chain) return null;
    const position = StructureProperties.residue.auth_seq_id(location);
    return { chain, position, residue: chain.residues?.find((r) => r.position === position) };
  }

  select(target, partners) {
    // Serialize Mol* state commits. Each callback reads the newest selection.
    this.selection = { target, partners: new Set(partners) };
    this.pending = this.pending.catch(() => {}).then(async () => {
      if (this.disposed) return;
      const { target, partners } = this.selection;
      const plugin = this.viewer.plugin, update = plugin.state.data.build();
      for (const [id, representation] of this.representations) {
        const selected = id === target || partners.has(id);
        update.to(representation).update((params) => ({
          ...params,
          colorTheme: { name: "uniform", params: { value: id === target ? 0x17695f : partners.has(id) ? 0x397acc : 0xabbac5 } },
          type: { ...params.type, params: { ...params.type.params, alpha: selected ? 1 : 0.55 } },
        }));
      }
      await update.commit();
      if (this.disposed) return;
      // Remove default per-residue selection marks after assigning chain roles.
      plugin.managers.interactivity.lociSelects.deselectAll();
    });
    return this.pending;
  }

  fit() {
    if (!this.disposed) this.viewer.plugin.managers.camera.reset();
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.viewer.dispose();
  }
};
