"""Minimal add-on used as a CI fixture for the blender-extension-builder action."""

import bpy


class BUILDERTEST_OT_noop(bpy.types.Operator):
    """Do nothing; exists so the fixture registers a real operator."""

    bl_idname = "builder_test.noop"
    bl_label = "Builder Test No-op"

    def execute(self, context):
        return {"FINISHED"}


def register():
    bpy.utils.register_class(BUILDERTEST_OT_noop)


def unregister():
    bpy.utils.unregister_class(BUILDERTEST_OT_noop)
