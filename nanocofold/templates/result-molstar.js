/* Mol* 5.12.0, local assets and saved coordinates only. */
"use strict";
window.NanoCoFoldResultViewer = class NanoCoFoldResultViewer {
  static async create(host, data, onPick) {
    const viewer = await molstar.Viewer.create(host, {
      extensions: [], layoutIsExpanded: false, layoutShowControls: true,
      layoutShowRemoteState: false, layoutShowSequence: true, layoutShowLog: false,
      layoutShowLeftPanel: false, collapseRightPanel: true, viewportShowExpand: false,
      viewportShowToggleFullscreen: false, viewportShowControls: true,
      viewportShowSelectionMode: true, viewportShowAnimation: false,
      viewportFocusBehavior: "disabled",
      volumeStreamingDisabled: true, viewportBackgroundColor: "#ffffff",
    });
    const result = new NanoCoFoldResultViewer(viewer, data);
    try {
      result.structure = await result.load(data.content, data.format, "예측 복합체");
      for (const chain of data.chains || [])
        await result.component(result.structure, {auth_asym_id: chain.id}, chain.role,
          `체인 ${chain.id} · ${chain.role}`, chain.role === "target" ? 0x3b82b2 : chain.role === "binder" ? 0xd79a2b : 0x909ea9,
          chain.residues.length < 4 ? "ball-and-stick" : "cartoon");
      for (const [key, labels, color, type] of [
        ["hotspots", data.hotspots || [], 0xcf446b, "ball-and-stick"],
        ["contacts", (data.contacts || []).flatMap(p => [p.a, p.b]), 0x16a092, "ball-and-stick"],
        ["clashes", (data.clashes || []).flatMap(p => [p.a, p.b]), 0xd83a31, "ball-and-stick"],
      ]) if (labels.length)
        await result.component(result.structure, result.schema(labels), key, key, color, type);
      const cdrColors = {CDR1: 0x4376d7, CDR2: 0x199a74, CDR3: 0xb750a5};
      for (const [name, region] of Object.entries(data.cdr?.ranges || {}))
        await result.component(result.structure, result.schema(region.labels), "cdr", name, cdrColors[name], "cartoon");
      if (data.reference?.available) {
        const reference = await result.load(data.reference.content, data.reference.format, "입력 참조 · 표적 Cα 정렬");
        await result.component(reference, {}, "reference", "보존된 입력 좌표", 0x697582, "cartoon", 0.35);
      }
      viewer.subscribe(viewer.plugin.behaviors.interaction.click, event => {
        if (result.disposed) return;
        const location = result.location(event.current?.loci);
        if (location) onPick(location);
      });
      await result.display({sequence: false, residues: false});
      await result.fit();
      result.observeResize(host);
      return result;
    } catch (error) { result.dispose(); throw error; }
  }
  constructor(viewer, data) {
    this.viewer = viewer; this.data = data; this.representations = new Map();
    this.disposed = false; this.pending = Promise.resolve();
    this.activeLayers = {target: true, binder: true, context: true};
    this.displayOptions = {sequence: false, residues: false};
    this.residuesCreated = false;
    this.cameraFocusLabels = null;
  }
  async load(content, format, label) {
    const plugin = this.viewer.plugin;
    const raw = await plugin.builders.data.rawData({data: content, label});
    const trajectory = await plugin.builders.structure.parseTrajectory(raw, format === "cif" ? "mmcif" : "pdb");
    const model = await plugin.builders.structure.createModel(trajectory);
    return plugin.builders.structure.createStructure(model, {name: "model", params: {}});
  }
  schema(labels, atom) {
    return {items: [...new Set(labels)].map(label => {
      const m = String(label).match(/^([^:]+):(-?\d+)([A-Za-z]?)$/);
      if (!m) throw Error(`잔기 번호 형식 오류: ${label}`);
      const residue = this.data.chains.find(c => c.id === m[1])?.residues.find(r => r.label === label);
      const atomId = atom && residue?.atom_ids?.[atom];
      return {auth_asym_id: m[1], auth_seq_id: Number(m[2]), pdbx_PDB_ins_code: m[3] || "",
        ...(atom ? {auth_atom_id: atom, ...(atomId != null ? {atom_id: atomId} : {})} : {})};
    })};
  }
  async component(structure, schema, key, label, color, type, alpha = 1) {
    const {StructureElement} = molstar.lib.structure, plugin = this.viewer.plugin;
    const loci = StructureElement.Loci.fromSchema(structure.obj.data, schema);
    if (StructureElement.Loci.isEmpty(loci)) return;
    const component = await plugin.builders.structure.tryCreateComponent(structure, {
      type: {name: "bundle", params: StructureElement.Bundle.fromLoci(loci)}, nullIfEmpty: true, label,
    }, `result-${key}-${this.representations.get(key)?.length || 0}`);
    if (!component) return;
    const repr = await plugin.builders.structure.representation.addRepresentation(component, {
      type, color: "uniform", colorParams: {value: color}, typeParams: {alpha},
    });
    if (!this.representations.has(key)) this.representations.set(key, []);
    this.representations.get(key).push(repr);
  }
  enqueue(operation) {
    this.pending = this.pending.catch(() => {}).then(() => this.disposed ? undefined : operation());
    return this.pending;
  }
  layers(active) {
    return this.enqueue(() => {
      this.activeLayers = {...active};
      this.applyVisibility();
    });
  }
  applyVisibility() {
    if (this.disposed) return;
    for (const [key, reprs] of this.representations) {
      const role = key.startsWith("residues-") ? key.slice(9) : key;
      const atoms = key.startsWith("residues-") || ["hotspots", "contacts", "clashes"].includes(key);
      const visible = !!this.activeLayers[role] && (!atoms || this.displayOptions.residues);
      for (const repr of reprs)
        this.viewer.plugin.state.data.updateCellState(repr.ref, {isHidden: !visible});
    }
  }
  display(options) {
    return this.enqueue(async () => {
      this.displayOptions = {sequence: options.sequence === true, residues: options.residues === true};
      const plugin = this.viewer.plugin;
      plugin.layout.setProps({regionState: {...plugin.layout.state.regionState,
        top: this.displayOptions.sequence ? "full" : "hidden"}});
      this.viewer.handleResize();
      if (this.displayOptions.residues && !this.residuesCreated) {
        for (const chain of this.data.chains || []) {
          if (this.disposed) return;
          await this.component(this.structure, {auth_asym_id: chain.id}, `residues-${chain.role}`,
            `체인 ${chain.id} · 잔기·곁사슬`, chain.role === "target" ? 0x3b82b2 : chain.role === "binder" ? 0xd79a2b : 0x909ea9,
            "ball-and-stick");
        }
        this.residuesCreated = true;
      }
      this.applyVisibility();
    });
  }
  clearSelection() {
    return this.enqueue(() => {
      const plugin = this.viewer.plugin;
      plugin.managers.interactivity.lociSelects.deselectAll();
      plugin.managers.interactivity.lociHighlights.clearHighlights();
      plugin.managers.structure.focus.clear();
      this.selectedLabels = [];
    });
  }
  loci(labels, atom) {
    const {StructureElement} = molstar.lib.structure;
    const loci = StructureElement.Loci.fromSchema(this.structure.obj.data, this.schema(labels, atom));
    if (StructureElement.Loci.isEmpty(loci)) throw Error("선택한 잔기·원자가 구조에 없습니다.");
    return loci;
  }
  focus(labels) {
    return this.enqueue(() => {
      const plugin = this.viewer.plugin, loci = this.loci(labels);
      plugin.managers.interactivity.lociSelects.deselectAll();
      plugin.managers.interactivity.lociSelects.select({loci});
      plugin.managers.camera.focusLoci(loci, window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? {durationMs:0} : undefined);
      this.selectedLabels = [...labels];
      this.cameraFocusLabels = [...labels];
    });
  }
  distance(a, b, atomA = "CA", atomB = "CA") {
    return this.enqueue(async () => {
      const lociA = this.loci([a], atomA), lociB = this.loci([b], atomB);
      await this.viewer.plugin.managers.structure.measurement.addDistance(lociA, lociB, {
        labelParams: {textSize: 0.7},
      });
      this.lastMeasurement = {a, b, atomA, atomB};
    });
  }
  location(loci) {
    const {StructureElement, StructureProperties, Bond} = molstar.lib.structure;
    if (loci?.kind === "bond-loci") loci = Bond.toStructureElementLoci(loci);
    if (!loci || !StructureElement.Loci.is(loci) || loci.structure.root !== this.structure.obj.data.root) return null;
    const location = StructureElement.Loci.getFirstLocation(loci);
    if (!location) return null;
    const chain = StructureProperties.chain.auth_asym_id(location), number = StructureProperties.residue.auth_seq_id(location);
    const icode = StructureProperties.residue.pdbx_PDB_ins_code(location) || "";
    const label = `${chain}:${number}${icode}`;
    const chainData = this.data.chains.find(c => c.id === chain);
    const index = chainData?.residues.findIndex(r => r.label === label);
    const mapping = chain !== this.data.binder_chain && index >= 0
      ? this.data.residue_mapping?.find(r => r.output_chain === chain && Number(r.output_position) === index + 1) : null;
    return {label, chain, number, icode, mapping};
  }
  fit() {
    return this.enqueue(() => {
      this.cameraFocusLabels = null;
      this.viewer.plugin.managers.camera.reset(undefined, 0);
    });
  }
  observeResize(host) {
    if (typeof ResizeObserver === "undefined") return;
    this.resizeObserver = new ResizeObserver(() => {
      cancelAnimationFrame(this.resizeFrame);
      this.viewer.handleResize();
      let remaining = 120;
      const fitWhenReady = () => {
        if (this.disposed || host.clientWidth === 0 || host.clientHeight === 0) return;
        const canvas = host.querySelector("canvas"), viewport = this.viewer.plugin.canvas3d?.camera.viewport;
        if (!canvas || !viewport) return;
        const rect = canvas.getBoundingClientRect();
        // MolStar resizes its drawing buffer after the CSS layout changes.
        // Fit only once the camera uses the new aspect ratio.
        if (Math.abs(viewport.width / viewport.height - rect.width / rect.height) > 0.002) {
          if (--remaining > 0) this.resizeFrame = requestAnimationFrame(fitWhenReady);
          return;
        }
        const camera = this.viewer.plugin.managers.camera;
        if (this.cameraFocusLabels?.length)
          camera.focusLoci(this.loci(this.cameraFocusLabels), {durationMs: 0});
        else camera.reset(undefined, 0);
      };
      this.resizeFrame = requestAnimationFrame(fitWhenReady);
    });
    this.resizeObserver.observe(host);
  }
  dispose() {
    if (!this.disposed) {
      this.disposed = true;
      this.resizeObserver?.disconnect();
      cancelAnimationFrame(this.resizeFrame);
      this.viewer.dispose();
    }
  }
};
