from django.db import migrations

def seed_hvac_template(apps, schema_editor):
    TaskTemplate = apps.get_model('projects', 'TaskTemplate')
    TaskTemplateItem = apps.get_model('projects', 'TaskTemplateItem')

    template = TaskTemplate.objects.create(
        name="HVAC Installation - Standard (28 Step)",
        description="Standard 28-step sequential workflow for HVAC installation projects."
    )

    steps = [
        (1, "Contract Award & Kick-off Meeting", "Project Manager"),
        (2, "Site Survey & Measurement", "Project Engineer"),
        (3, "Load Calculation Review", "Design Engineer"),
        (4, "Shop Drawing Preparation", "Design Team"),
        (5, "Client/Consultant Approval", "Project Manager"),
        (6, "Material Procurement", "Procurement Team"),
        (7, "Factory Inspection (if required)", "QA/QC Engineer"),
        (8, "Material Delivery to Site", "Logistics Team"),
        (9, "Duct Fabrication", "Duct Team"),
        (10, "Duct Installation", "HVAC Installation Team"),
        (11, "Chilled Water Pipe/Refrigerant Pipe Installation", "Piping Team"),
        (12, "Drain Pipe Installation", "Plumbing Team"),
        (13, "Cable Tray Installation", "Electrical Team"),
        (14, "Power & Control Cable Installation", "Electrical Team"),
        (15, "Indoor Unit (IDU) Installation", "HVAC Team"),
        (16, "Outdoor Unit (ODU) Installation", "HVAC Team"),
        (17, "Fresh Air & Exhaust Fan Installation", "Mechanical Team"),
        (18, "Insulation Work", "Insulation Team"),
        (19, "Pressure Test & Leak Test", "QA/QC Engineer"),
        (20, "Nitrogen Flushing & Vacuuming", "Commissioning Team"),
        (21, "Electrical Termination", "Electrical Team"),
        (22, "Testing & Commissioning", "Commissioning Engineer"),
        (23, "Air Balancing & System Calibration", "TAB Team"),
        (24, "Client Inspection", "Project Manager"),
        (25, "Snag Rectification", "Site Team"),
        (26, "Final Handover", "Project Manager"),
        (27, "Training for Client's Technical Team", "Service Engineer"),
        (28, "Warranty & Maintenance Support", "Service Department"),
    ]

    for order, activity, role in steps:
        TaskTemplateItem.objects.create(
            template=template,
            order=order,
            activity=activity,
            default_responsible_role=role,
            default_duration_days=5  # Default 5 days per step to allow sequential scheduling
        )

def remove_hvac_template(apps, schema_editor):
    TaskTemplate = apps.get_model('projects', 'TaskTemplate')
    TaskTemplate.objects.filter(name="HVAC Installation - Standard (28 Step)").delete()

class Migration(migrations.Migration):

    dependencies = [
        ('projects', '0002_tasktemplate_projecttask_tasktemplateitem'),
    ]

    operations = [
        migrations.RunPython(seed_hvac_template, remove_hvac_template),
    ]
